"""Records a Perfetto trace plus the sidecar CSV from a USB-connected Android device.

Output: <out>.pftrace and <out>.sidecar.csv. Python drives adb directly, so
this works the same on Windows/macOS/Linux (and avoids Git Bash rewriting
/data/... paths).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "game.pbtx"
SIDECAR_SCRIPT = Path(__file__).resolve().parent / "device" / "sidecar.sh"

REMOTE_DIR = "/data/local/tmp"
REMOTE_SCRIPT = f"{REMOTE_DIR}/gpa_sidecar.sh"
REMOTE_FLAG = f"{REMOTE_DIR}/gpa_run"
REMOTE_CSV = f"{REMOTE_DIR}/gpa_sidecar.csv"
# traced can only write into this directory on user builds.
REMOTE_TRACE = "/data/misc/perfetto-traces/gpa_trace.pftrace"


class RecordError(RuntimeError):
    pass


def find_adb(explicit: str | None = None) -> str:
    candidates = [explicit, shutil.which("adb")]
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env):
            candidates.append(str(Path(os.environ[env]) / "platform-tools" / "adb"))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(str(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk" / "platform-tools" / "adb.exe"))
    for c in candidates:
        if c and (Path(c).exists() or Path(c + ".exe").exists() or shutil.which(c)):
            return c
    raise RecordError("adb not found; install Android platform-tools or pass --adb")


class Adb:
    def __init__(self, adb: str, serial: str | None = None):
        self.base = [adb] + (["-s", serial] if serial else [])

    def run(self, *args: str, input: str | None = None, check: bool = True, timeout: float | None = None) -> str:
        p = subprocess.run(self.base + list(args), input=input, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        if check and p.returncode != 0:
            raise RecordError(f"adb {' '.join(args)} failed: {(p.stderr or p.stdout).strip()}")
        return p.stdout + p.stderr

    def shell(self, cmd: str, **kw) -> str:
        return self.run("shell", cmd, **kw)


def render_config(config_text: str, duration_s: int) -> str:
    text, n = re.subn(r"^duration_ms:\s*\d+", f"duration_ms: {duration_s * 1000}", config_text, flags=re.M)
    if n != 1:
        raise RecordError("config must contain exactly one top-level duration_ms line")
    return text


def record(
    out: str | Path,
    duration_s: int = 30,
    config: str | Path = DEFAULT_CONFIG,
    sidecar: bool = True,
    period_s: float = 0.5,
    adb_path: str | None = None,
    serial: str | None = None,
    log=print,
) -> tuple[Path, Path | None]:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    trace_path = out.with_suffix(".pftrace")
    csv_path = out.with_suffix(".sidecar.csv")

    adb = Adb(find_adb(adb_path), serial)
    state = _ensure_online(adb, log)
    if state != "device":
        raise RecordError(f"no authorized device (adb get-state: {state!r}); enable USB debugging and accept the prompt")

    cfg = render_config(Path(config).read_text(encoding="utf-8"), duration_s)
    adb.shell(f"rm -f {REMOTE_TRACE} {REMOTE_CSV} {REMOTE_FLAG}", check=False)

    if sidecar:
        adb.run("push", str(SIDECAR_SCRIPT), REMOTE_SCRIPT)
        adb.shell(f"touch {REMOTE_FLAG}")
        adb.shell(f"nohup sh {REMOTE_SCRIPT} {REMOTE_CSV} {REMOTE_FLAG} {period_s} 4 >/dev/null 2>&1 &")
        _wait_for_sidecar(adb)
        log("sidecar sampler running")

    log(f"recording {duration_s}s trace ... (play the game now)")
    try:
        adb.shell(f"perfetto --txt -c - -o {REMOTE_TRACE}", input=cfg, timeout=duration_s + 120)
    finally:
        if sidecar:
            adb.shell(f"rm -f {REMOTE_FLAG}", check=False)
            time.sleep(period_s * 3)

    adb.run("pull", REMOTE_TRACE, str(trace_path))
    pulled_csv = None
    if sidecar:
        adb.run("pull", REMOTE_CSV, str(csv_path))
        pulled_csv = csv_path
    adb.shell(f"rm -f {REMOTE_TRACE} {REMOTE_CSV} {REMOTE_SCRIPT} {REMOTE_FLAG}", check=False)
    log(f"wrote {trace_path}" + (f" and {csv_path}" if pulled_csv else ""))
    return trace_path, pulled_csv


def _ensure_online(adb: Adb, log) -> str:
    """Returns the device state, restarting the adb server once if the device is 'offline'.

    On a Galaxy S24 over USB the device repeatedly dropped to 'offline' after
    being idle or re-plugged; `adb reconnect offline` or a server restart recovered it.
    """
    state = adb.run("get-state", check=False).strip()
    if state == "device":
        return state
    log(f"adb state {state!r}; trying `adb reconnect offline`")
    adb.run("reconnect", "offline", check=False, timeout=15)
    time.sleep(3)
    state = adb.run("get-state", check=False).strip()
    if state == "device":
        return state
    log("restarting adb server")
    adb.run("kill-server", check=False)
    adb.run("start-server", check=False)
    adb.run("wait-for-device", check=False, timeout=15)
    return adb.run("get-state", check=False).strip()


def _wait_for_sidecar(adb: Adb, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        lines = adb.shell(f"cat {REMOTE_CSV} 2>/dev/null | wc -l", check=False).strip()
        if lines.isdigit() and int(lines) > 2:
            return
        time.sleep(0.5)
    raise RecordError("sidecar sampler did not start (no output after 15 s)")
