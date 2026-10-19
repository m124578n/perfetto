"""GPU frequency and utilization.

Prefers the trace (gpu_counter_track 'gpufreq', from the power/gpu_frequency
ftrace event, in kHz). Falls back to the sidecar (sysfs gpu_clock / gpu_busy),
which is the only source on devices whose kernel lacks that event (Galaxy S24).
"""

from __future__ import annotations

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._timeseries import Sample, clip_steps, thirds, time_share, time_weighted_mean
from gameperf_agent.metrics._util import rows
from gameperf_agent.schema import GpuMetrics, SeriesSegment
from gameperf_agent.sidecar import Sidecar

NEAR_MAX = 0.95
BUSY_HIGH_PCT = 90.0

_TRACE_SQL = """
select c.ts, c.value
from counter c join gpu_counter_track t on c.track_id = t.id
where t.name = 'gpufreq'
order by c.ts
"""


def extract_gpu(tp: TraceProcessor, sidecar: Sidecar | None, window: tuple[int, int]) -> GpuMetrics:
    start, end = window
    freq = [Sample(r.ts, r.value / 1000.0) for r in rows(tp, _TRACE_SQL)]
    source, hw_max, busy, cap, thermal = "trace", None, [], [], []
    if sidecar is not None:
        hw_max = sidecar.static.get("gpu_hw_max_mhz")
        busy = sidecar.series.get("gpu_busy_pct", [])
        cap = sidecar.series.get("gpu_cap_mhz", [])
        thermal = sidecar.series.get("gpu_thermal_pwrlevel", [])
        if not freq:
            freq = sidecar.series.get("gpu_clock_mhz", [])
            source = "sidecar"
    pieces = clip_steps(freq, start, end)
    if not pieces:
        return GpuMetrics(
            available=False,
            reason="no GPU frequency: trace lacks power/gpu_frequency and no sidecar data in window",
        )

    values = [v for _, _, v in pieces]
    hw_max = hw_max or max(values)
    busy_pieces = clip_steps(busy, start, end)
    cap_pieces = clip_steps(cap, start, end)
    thermal_pieces = clip_steps(thermal, start, end)
    return GpuMetrics(
        available=True,
        source=source,
        hw_max_mhz=hw_max,
        min_mhz=min(values),
        max_mhz=max(values),
        avg_mhz=time_weighted_mean(pieces),
        time_at_hw_max_ratio=time_share(pieces, lambda v: v >= NEAR_MAX * hw_max),
        avg_busy_pct=time_weighted_mean(busy_pieces),
        time_busy_high_ratio=time_share(busy_pieces, lambda v: v >= BUSY_HIGH_PCT),
        cap_min_mhz=min((v for _, _, v in cap_pieces), default=None),
        capped_time_ratio=time_share(cap_pieces, lambda v: v < NEAR_MAX * hw_max),
        thermal_pwrlevel_max=max((v for _, _, v in thermal_pieces), default=None),
        segments=[
            SeriesSegment(
                label=label,
                avg=time_weighted_mean(seg := clip_steps(freq, lo, hi)),
                max=max((v for _, _, v in seg), default=None),
            )
            for label, lo, hi in thirds(start, end)
        ],
    )
