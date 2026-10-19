"""Where and how the game's key threads run: CPU cluster placement and scheduler states.

Thread names are the kernel comm, truncated to 15 characters
(e.g. "UnityGfxDeviceWorker" -> "UnityGfxDeviceW"), hence the prefix globs.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._util import rows
from gameperf_agent.schema import CpuTopology, ThreadMetrics, ThreadsMetrics

# (glob, role). Unity, Unreal, and generic GL/Vulkan render loops.
NAME_RULES: list[tuple[str, str]] = [
    ("UnityMain", "game_logic"),
    ("GameThread", "game_logic"),
    ("GLThread*", "game_logic"),
    ("UnityGfxDeviceW*", "render"),
    ("RenderThread*", "render"),
    ("RHIThread*", "render"),
]
TOP_CPU_LIMIT = 5
TOP_CPU_MIN_UTIL = 0.05

# Clips [ts, ts+dur) to the window; dur = -1 means "until the end of the trace".
_CLIPPED = "max(0, min(ts + (case when dur < 0 then {end} - ts else dur end), {end}) - max(ts, {start}))"

_THREADS_SQL = """
select t.utid, t.tid, t.name, coalesce(t.is_main_thread, 0) as is_main
from thread t where t.upid = {upid}
"""

_STATES_SQL = """
select utid, state, sum({clipped}) as ns
from thread_state
where utid in (select utid from thread where upid = {upid})
group by utid, state
"""

_RUNNING_BY_CPU_SQL = """
select utid, cpu, sum({clipped}) as ns
from sched
where utid in (select utid from thread where upid = {upid})
group by utid, cpu
"""

# Not trace_bounds: on a real S24 trace it started ~37 s before ftrace data
# (stale events from other data sources), which made a 10 s recording look 47 s long.
_BOUNDS_SQL = "select min(ts) as start_ts, max(ts + max(dur, 0)) as end_ts from sched"


def _role(name: str | None) -> str | None:
    if not name:
        return None
    for pattern, role in NAME_RULES:
        if fnmatch.fnmatchcase(name, pattern):
            return role
    return None


def extract_threads(
    tp: TraceProcessor,
    upid: int | None,
    topology: CpuTopology,
    window: tuple[int, int] | None = None,
) -> ThreadsMetrics:
    if upid is None:
        return ThreadsMetrics(available=False, reason="no target process")
    if not rows(tp, "select 1 from sched limit 1"):
        return ThreadsMetrics(available=False, reason="no scheduling data (enable ftrace sched/sched_switch)")

    if window is None:
        b = rows(tp, _BOUNDS_SQL)[0]
        window = (b.start_ts, b.end_ts)
    start, end = window
    window_ns = end - start
    clipped = _CLIPPED.format(start=start, end=end)

    states: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows(tp, _STATES_SQL.format(upid=int(upid), clipped=clipped)):
        states[r.utid][r.state] += r.ns

    cluster_of = topology.cluster_name_by_cpu()
    by_cluster: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows(tp, _RUNNING_BY_CPU_SQL.format(upid=int(upid), clipped=clipped)):
        by_cluster[r.utid][cluster_of.get(r.cpu, f"cpu{r.cpu}")] += r.ns

    def running(utid: int) -> float:
        return sum(by_cluster[utid].values())

    selected: dict[int, tuple[str, str]] = {}
    threads = rows(tp, _THREADS_SQL.format(upid=int(upid)))
    min_running = TOP_CPU_MIN_UTIL * window_ns
    # Threads inherit their creator's name until renamed, so a real Unity trace
    # had 20 mostly idle "UnityMain"s. Per role keep the busiest name match, plus
    # any other match that is itself busy.
    busiest_by_role: dict[str, int] = {}
    for t in sorted(threads, key=lambda t: running(t.utid), reverse=True):
        role = _role(t.name)
        if role and (role not in busiest_by_role or running(t.utid) >= min_running):
            busiest_by_role.setdefault(role, t.utid)
            selected[t.utid] = (role, "name")
        elif t.is_main and not role:
            selected[t.utid] = ("main", "main_thread")
    others = sorted((t for t in threads if t.utid not in selected), key=lambda t: running(t.utid), reverse=True)
    for t in others[:TOP_CPU_LIMIT]:
        if running(t.utid) >= TOP_CPU_MIN_UTIL * window_ns:
            selected[t.utid] = ("top_cpu", "top_cpu")

    out = []
    for t in threads:
        if t.utid not in selected:
            continue
        role, how = selected[t.utid]
        st = states[t.utid]
        run_ns = running(t.utid)
        runnable_ns = st.get("R", 0.0) + st.get("R+", 0.0)
        d_ns = sum(v for k, v in st.items() if k.startswith("D"))
        out.append(
            ThreadMetrics(
                utid=t.utid,
                tid=t.tid,
                name=t.name,
                role=role,
                identified_by=how,
                running_ms=run_ns / 1e6,
                runnable_ms=runnable_ns / 1e6,
                uninterruptible_ms=d_ns / 1e6,
                sleeping_ms=st.get("S", 0.0) / 1e6,
                cpu_util=run_ns / window_ns if window_ns else 0.0,
                runnable_ratio=runnable_ns / (run_ns + runnable_ns) if run_ns + runnable_ns else None,
                cluster_share={k: v / run_ns for k, v in by_cluster[t.utid].items()} if run_ns else {},
            )
        )
    out.sort(key=lambda m: m.running_ms, reverse=True)

    process_by_cluster: dict[str, float] = defaultdict(float)
    for clusters in by_cluster.values():
        for name, ns in clusters.items():
            process_by_cluster[name] += ns
    total = sum(process_by_cluster.values())

    return ThreadsMetrics(
        available=True,
        window_ms=window_ns / 1e6,
        threads=out,
        process_running_ms=total / 1e6,
        process_cluster_share={k: v / total for k, v in process_by_cluster.items()} if total else {},
    )
