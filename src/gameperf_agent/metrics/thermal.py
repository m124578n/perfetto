"""Thermal state and frequency caps.

Sources, merged:
- trace counter tracks of type thermal_temperature / thermal_temperature_sys
  (ftrace thermal/thermal_temperature, sys_stats polling; often empty on user builds)
- sidecar: thermal zone temps (milli-C), Android Thermal Status (0-6, from
  dumpsys thermalservice), and scaling_max_freq per cpufreq policy (a cap
  below the hardware max is how thermal mitigation throttles a CPU).
"""

from __future__ import annotations

import re
from collections import defaultdict

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._timeseries import Sample, clip_steps, time_share
from gameperf_agent.metrics._util import rows
from gameperf_agent.schema import CpuCap, CpuTopology, ThermalMetrics, ZoneTemp
from gameperf_agent.sidecar import Sidecar, policy_cpu

CAPPED_BELOW = 0.95  # scaling_max / hw_max; some OEMs ship a static cap ~0.98
STATUS_LIGHT, STATUS_SEVERE = 1, 3

_TRACE_TEMPS_SQL = """
select t.name, c.ts, c.value
from counter c join counter_track t on c.track_id = t.id
where t.type in ('thermal_temperature', 'thermal_temperature_sys')
order by c.ts
"""


def _zone_group(name: str) -> str:
    """'gpuss-3' -> 'gpuss', 'cpu-1-2-0' -> 'cpu', 'soc Temperature' -> 'soc'."""
    name = name.removesuffix(" Temperature")
    return re.sub(r"(-\d+)+$", "", name)


def _zone_stats(zone: str, source: str, samples: list[Sample], start: int, end: int) -> ZoneTemp | None:
    inside = [s for s in samples if start <= s.ts <= end]
    if not inside:
        return None
    first, last = inside[0], inside[-1]
    minutes = (last.ts - first.ts) / 60e9
    return ZoneTemp(
        zone=zone,
        source=source,
        start_c=first.value,
        end_c=last.value,
        max_c=max(s.value for s in inside),
        delta_c=last.value - first.value,
        slope_c_per_min=(last.value - first.value) / minutes if minutes > 0 else None,
    )


def _group_max(series: dict[str, list[Sample]], scale: float) -> dict[str, list[Sample]]:
    """Merges member zones into a group series: per timestamp, the hottest member."""
    grouped: dict[str, dict[int, float]] = defaultdict(dict)
    for name, samples in series.items():
        g = grouped[_zone_group(name)]
        for s in samples:
            v = s.value * scale
            g[s.ts] = max(g.get(s.ts, v), v)
    return {k: [Sample(ts, v) for ts, v in sorted(d.items())] for k, d in grouped.items()}


def extract_thermal(
    tp: TraceProcessor, sidecar: Sidecar | None, topology: CpuTopology, window: tuple[int, int]
) -> ThermalMetrics:
    start, end = window
    zones: list[ZoneTemp] = []

    trace_series: dict[str, list[Sample]] = defaultdict(list)
    for r in rows(tp, _TRACE_TEMPS_SQL):
        trace_series[r.name].append(Sample(r.ts, r.value))
    for name, samples in _group_max(trace_series, 1.0).items():
        if z := _zone_stats(name, "trace", samples, start, end):
            zones.append(z)

    status_pieces: list = []
    caps: list[CpuCap] = []
    if sidecar is not None:
        for name, samples in _group_max(sidecar.with_prefix("temp_mc:"), 0.001).items():
            if z := _zone_stats(name, "sidecar", samples, start, end):
                zones.append(z)
        status_pieces = clip_steps(sidecar.series.get("thermal_status", []), start, end)
        cluster_of = topology.cluster_name_by_cpu()
        for policy, samples in sorted(sidecar.with_prefix("max_khz:").items(), key=lambda kv: policy_cpu(kv[0])):
            hw = sidecar.static.get(f"hw_max_khz:{policy}")
            pieces = clip_steps(samples, start, end)
            if not hw or not pieces:
                continue
            caps.append(
                CpuCap(
                    cluster=cluster_of.get(policy_cpu(policy), policy),
                    policy=policy,
                    hw_max_mhz=hw / 1000,
                    min_cap_mhz=min(v for _, _, v in pieces) / 1000,
                    min_cap_ratio=min(v for _, _, v in pieces) / hw,
                    capped_time_ratio=time_share(pieces, lambda v, hw=hw: v < CAPPED_BELOW * hw),
                )
            )

    if not zones and not status_pieces and not caps:
        return ThermalMetrics(
            available=False,
            reason="no thermal data: trace has no thermal counters and no sidecar data in window",
        )
    return ThermalMetrics(
        available=True,
        zones=sorted(zones, key=lambda z: -z.max_c),
        thermal_status_max=max((v for _, _, v in status_pieces), default=None),
        thermal_status_ge_light_ratio=time_share(status_pieces, lambda v: v >= STATUS_LIGHT),
        thermal_status_ge_severe_ratio=time_share(status_pieces, lambda v: v >= STATUS_SEVERE),
        cpu_caps=caps,
    )
