"""Per-cluster CPU frequency (DVFS) behaviour over the analysis window.

CPUs in a cluster share one frequency domain, so the cluster value is the
mean across its CPUs' step functions (they are normally identical).
"""

from __future__ import annotations

from collections import defaultdict

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._timeseries import Sample, clip_steps, thirds, time_share, time_weighted_mean
from gameperf_agent.metrics._util import rows
from gameperf_agent.schema import ClusterFreq, CpuFreqMetrics, CpuTopology, SeriesSegment

NEAR_MAX = 0.95

_SQL = """
select t.cpu, c.ts, c.value
from counter c join cpu_counter_track t on c.track_id = t.id
where t.name = 'cpufreq'
order by t.cpu, c.ts
"""


def extract_cpufreq(tp: TraceProcessor, topology: CpuTopology, window: tuple[int, int]) -> CpuFreqMetrics:
    by_cpu: dict[int, list[Sample]] = defaultdict(list)
    for r in rows(tp, _SQL):
        by_cpu[r.cpu].append(Sample(r.ts, r.value / 1000.0))  # kHz -> MHz
    if not by_cpu:
        return CpuFreqMetrics(available=False, reason="no cpufreq counters (enable power/cpu_frequency)")
    if not topology.available:
        return CpuFreqMetrics(available=False, reason=f"cpu topology unavailable: {topology.reason}")

    start, end = window
    out = []
    for cluster in topology.clusters:
        pieces = [p for cpu in cluster.cpus for p in clip_steps(by_cpu.get(cpu, []), start, end)]
        if not pieces:
            continue
        values = [v for _, _, v in pieces]
        hw_max = cluster.max_freq_khz / 1000.0 if cluster.max_freq_khz else max(values)
        segments = []
        for label, lo, hi in thirds(start, end):
            seg = [p for cpu in cluster.cpus for p in clip_steps(by_cpu.get(cpu, []), lo, hi)]
            segments.append(
                SeriesSegment(
                    label=label,
                    avg=time_weighted_mean(seg),
                    max=max((v for _, _, v in seg), default=None),
                )
            )
        early_max, late_max = segments[0].max, segments[-1].max
        out.append(
            ClusterFreq(
                cluster=cluster.name,
                cpus=cluster.cpus,
                hw_max_mhz=hw_max,
                min_mhz=min(values),
                max_mhz=max(values),
                avg_mhz=time_weighted_mean(pieces),
                avg_vs_hw_max=time_weighted_mean(pieces) / hw_max,
                time_at_hw_max_ratio=time_share(pieces, lambda v: v >= NEAR_MAX * hw_max),
                late_vs_early_max_ratio=late_max / early_max if early_max and late_max else None,
                segments=segments,
            )
        )
    return CpuFreqMetrics(available=bool(out), reason=None if out else "no cpufreq samples in window", clusters=out)
