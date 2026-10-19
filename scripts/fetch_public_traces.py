"""Downloads public Android traces from Perfetto's test-data bucket into traces/public/.

These are NOT redistributed in this repo: Perfetto's source is Apache-2.0, but
I could not find an explicit license statement for the test-data bucket, so the
files are fetched from the source and verified by SHA-256 instead.

None of them is a game. They are useful for smoke-testing the pipeline
(FrameTimeline, sched, cpufreq, GPU freq on Pixel devices), not for the eval.

Usage: uv run python scripts/fetch_public_traces.py [name ...]
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

BUCKET = "https://storage.googleapis.com/perfetto/test_data"
OUT = Path(__file__).resolve().parents[1] / "traces" / "public"

# name -> (sha256 from google/perfetto test/data/<name>.sha256, what it contains)
TRACES = {
    "android_postboot_unlock.pftrace": (
        "445cd34686c12847640c26dee271069383d2e25c8a8c0e36f0b1ed8d94514488",
        "Pixel 7, Android 15: unlock after boot; FrameTimeline with several jank types, cpufreq, gpufreq",
    ),
    "webview_jank.pb": (
        "c9c268e09755625d8b5fad404ccca2e657a8b061417d7e10f03984887e942548",
        "Pixel 6, Android 13: Gmail WebView scrolling with jank; FrameTimeline + sched",
    ),
    "wattson_jank_cuj.pb": (
        "dc1a17d76931b24cf4e07a2f13f6072145e783215321457d31c7e74474b51d1a",
        "Pixel 6, Android 15: SystemUI jank CUJ; cpufreq, cpuidle, gpufreq, FrameTimeline",
    ),
    "wattson_gpu.pb": (
        "88efa9a9f1fcebcf0902a0947eeed6c895cd06137e7473b07ff2b05fdc5fef05",
        "Pixel 6 Pro, Android 16: GPU-heavy test app; gpufreq, cpufreq, sched",
    ),
    "example_android_trace_30s.pb": (
        "62656cc8ea2a624db95087850245d16453f0723f723ed2e329080e771bc1f82c",
        "Pixel, 30 s system trace; sched-heavy, no FrameTimeline",
    ),
}


def fetch(name: str) -> Path:
    digest, _ = TRACES[name]
    dest = OUT / name
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == digest:
        print(f"ok (cached) {dest}")
        return dest
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(f"{BUCKET}/{name}-{digest}", tmp)
    actual = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if actual != digest:
        tmp.unlink()
        raise SystemExit(f"sha256 mismatch for {name}: {actual}")
    tmp.replace(dest)
    print(f"ok {dest}")
    return dest


def main(names: list[str]) -> int:
    for name in names or TRACES:
        if name not in TRACES:
            print(f"unknown trace {name}; choose from: {', '.join(TRACES)}", file=sys.stderr)
            return 1
        fetch(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
