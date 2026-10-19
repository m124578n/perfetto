"""Reads the sidecar CSV written by device/sidecar.sh and checks its time alignment.

Format (long): uptime_s,key,value. uptime_s is CLOCK_BOOTTIME seconds, which
is Perfetto's default trace clock, so ts_ns = uptime_s * 1e9.
The first rows (header block) hold static keys such as gpu_hw_max_mhz and
hw_max_khz:policyN; later rows are periodic samples.
"""

from __future__ import annotations

import csv
import io
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._timeseries import Sample
from gameperf_agent.metrics._util import rows
from gameperf_agent.schema import SidecarAlignment

STATIC_KEYS = ("gpu_hw_max_mhz", "hw_max_khz:")
# The sampler stamps uptime first, then reads ~20 files; a value can be read
# up to tens of ms after its timestamp.
READ_TOLERANCE_NS = 50_000_000
BASELINE_OFFSETS_NS = (-2_000_000_000, -1_000_000_000, 1_000_000_000, 2_000_000_000)


@dataclass
class Sidecar:
    static: dict[str, float] = field(default_factory=dict)
    series: dict[str, list[Sample]] = field(default_factory=dict)

    def with_prefix(self, prefix: str) -> dict[str, list[Sample]]:
        return {k[len(prefix):]: v for k, v in self.series.items() if k.startswith(prefix)}


def parse_sidecar(text: str) -> Sidecar:
    static: dict[str, float] = {}
    series: dict[str, list[Sample]] = defaultdict(list)
    for row in csv.reader(io.StringIO(text)):
        if len(row) != 3 or row[0] == "uptime_s":
            continue
        try:
            ts = int(round(float(row[0]) * 1e9))
            value = float(row[2])
        except ValueError:
            continue
        key = row[1]
        if key.startswith(STATIC_KEYS):
            static.setdefault(key, value)
        else:
            series[key].append(Sample(ts, value))
    for samples in series.values():
        samples.sort(key=lambda s: s.ts)
    return Sidecar(static=static, series=dict(series))


def load_sidecar(path: str | Path | None) -> Sidecar | None:
    if not path:
        return None
    return parse_sidecar(Path(path).read_text(encoding="utf-8", errors="replace"))


def policy_cpu(policy: str) -> int:
    """'policy7' -> 7 (a cpufreq policy is named after its first cpu)."""
    return int(policy.removeprefix("policy"))


_TRACE_FREQ_SQL = """
select t.cpu, c.ts, c.value
from counter c join cpu_counter_track t on c.track_id = t.id
where t.name = 'cpufreq'
order by t.cpu, c.ts
"""


def alignment_check(tp: TraceProcessor, sidecar: Sidecar | None) -> SidecarAlignment:
    """Compares sidecar scaling_cur_freq samples with trace cpufreq.

    A sample matches if the trace shows that frequency at its timestamp or
    within READ_TOLERANCE_NS after it. The same score with the sidecar shifted
    by +/-1-2 s is the baseline: on an idle device frequencies rarely change and
    any offset scores high, so only match_ratio well above baseline is evidence
    of alignment. (Validated on a Galaxy S24 game trace: 1.00 at offset 0 vs
    0.76-0.85 when shifted.)
    """
    if sidecar is None:
        return SidecarAlignment(available=False, reason="no sidecar")
    trace: dict[int, tuple[list[int], list[float]]] = defaultdict(lambda: ([], []))
    for r in rows(tp, _TRACE_FREQ_SQL):
        trace[r.cpu][0].append(r.ts)
        trace[r.cpu][1].append(r.value)
    if not trace:
        return SidecarAlignment(available=False, reason="trace has no cpufreq to compare against")

    compared, ratio = _match(trace, sidecar, 0)
    if not compared:
        return SidecarAlignment(available=False, reason="sidecar and trace do not overlap in time")
    shifted = [r for n, r in (_match(trace, sidecar, off) for off in BASELINE_OFFSETS_NS) if n]
    return SidecarAlignment(
        available=True,
        compared_samples=compared,
        match_ratio=ratio,
        shifted_baseline_ratio=sum(shifted) / len(shifted) if shifted else None,
    )


def _match(trace: dict[int, tuple[list[int], list[float]]], sidecar: Sidecar, offset: int) -> tuple[int, float]:
    compared = matched = 0
    for policy, samples in sidecar.with_prefix("cur_khz:").items():
        ts_list, values = trace.get(policy_cpu(policy), ([], []))
        if not ts_list:
            continue
        for s in samples:
            t = s.ts + offset
            if not ts_list[0] <= t <= ts_list[-1]:
                continue
            i = bisect_right(ts_list, t) - 1
            j = bisect_right(ts_list, t + READ_TOLERANCE_NS)
            compared += 1
            matched += s.value in values[i:j]
    return compared, (matched / compared if compared else 0.0)
