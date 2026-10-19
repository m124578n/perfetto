"""CPU cluster topology, ranked from weakest to strongest.

The stdlib android_cpu_cluster_mapping is not used: on a Galaxy S24 it folds
two distinct clusters (cpu2-4 @3.15 GHz, cpu5-6 @2.96 GHz) into "medium".
Clusters come from cpu.cluster_id (derived by trace_processor from identical
frequency tables) and are ranked by, in order of preference: capacity,
hardware max frequency (linux.system_info), max frequency observed in trace.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._util import rows
from gameperf_agent.schema import CpuCluster, CpuTopology

_NAMES = {1: ["all"], 2: ["little", "big"], 3: ["little", "mid", "big"], 4: ["little", "mid", "big", "prime"]}

_CPU_SQL = """
select c.cpu, c.cluster_id, c.capacity,
       (select max(f.freq) from cpu_available_frequencies f where f.ucpu = c.ucpu) as max_avail
from cpu c
order by c.cpu
"""

_OBSERVED_SQL = """
select t.cpu, max(c.value) as max_obs
from counter c join cpu_counter_track t on c.track_id = t.id
where t.name = 'cpufreq'
group by t.cpu
"""


@dataclass
class _Cpu:
    cpu: int
    capacity: int | None
    max_avail: int | None


def _names(n: int) -> list[str]:
    if n in _NAMES:
        return _NAMES[n]
    return ["little"] + [f"mid{i}" for i in range(1, n - 2)] + ["big", "prime"]


def extract_topology(tp: TraceProcessor) -> CpuTopology:
    observed = {r.cpu: int(r.max_obs) for r in rows(tp, _OBSERVED_SQL)}
    cpus = rows(tp, _CPU_SQL)

    groups: dict[int, list[_Cpu]] = defaultdict(list)
    # cluster_id is only meaningful when cpu_info was recorded; otherwise the
    # cpu table is synthesized from events and every cpu sits in cluster 0.
    has_cpu_info = any(r.max_avail or r.capacity for r in cpus)
    if has_cpu_info and all(r.cluster_id is not None for r in cpus):
        for r in cpus:
            groups[r.cluster_id].append(_Cpu(r.cpu, r.capacity, r.max_avail))
    elif observed:
        # No cpu_info: group cpus that reached the same max frequency.
        for cpu, mx in observed.items():
            groups[mx].append(_Cpu(cpu, None, None))
    else:
        return CpuTopology(available=False, reason="no cpu topology (cpu_info) or cpufreq data in trace")

    members = list(groups.values())
    if all(m[0].capacity for m in members):
        basis, key = "capacity", lambda m: m[0].capacity
    elif all(m[0].max_avail for m in members):
        basis, key = "max_available_freq", lambda m: m[0].max_avail
    elif all(observed.get(r.cpu) for m in members for r in m):
        basis, key = "observed_max_freq", lambda m: max(observed[r.cpu] for r in m)
    else:
        basis, key = "cpu_index", lambda m: min(r.cpu for r in m)

    ranked = sorted(members, key=key)
    clusters = [
        CpuCluster(
            name=name,
            rank=i,
            cpus=sorted(r.cpu for r in m),
            capacity=m[0].capacity,
            max_freq_khz=m[0].max_avail or max((observed.get(r.cpu, 0) for r in m), default=0) or None,
        )
        for i, (name, m) in enumerate(zip(_names(len(ranked)), ranked))
    ]
    return CpuTopology(available=True, ordering_basis=basis, clusters=clusters)
