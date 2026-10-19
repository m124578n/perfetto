"""Chooses which process in the trace is the game."""

from __future__ import annotations

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._util import rows, sql_str
from gameperf_agent.schema import TargetProcess

SURFACEFLINGER = "/system/bin/surfaceflinger"

# Two frame signals per process:
#  - app surface frames in FrameTimeline (HWUI apps, some engines);
#  - outermost atrace queueBuffer slices (every producer, including Unity/GLES
#    games that never appear in FrameTimeline - verified on a real Unity trace).
# queueBuffer nests inside queueBuffer in real traces, so only the outer one counts.
_FRAME_SIGNALS = """
with tl as (
  select upid, count(*) as n from actual_frame_timeline_slice
  where layer_name is not null group by upid
),
qb as (
  select t.upid, count(*) as n
  from slice s join thread_track tt on s.track_id = tt.id join thread t using (utid)
  where s.name = 'queueBuffer'
    and (s.parent_id is null or (select p.name from slice p where p.id = s.parent_id) != 'queueBuffer')
  group by t.upid
)
select p.upid, p.pid, p.name, coalesce(tl.n, 0) as tl_frames, coalesce(qb.n, 0) as qb_frames
from process p left join tl using (upid) left join qb using (upid)
{where}
order by max(coalesce(tl.n, 0), coalesce(qb.n, 0)) desc, p.upid desc
"""


def _describe(r) -> str:
    return f"{r.tl_frames} FrameTimeline frames, {r.qb_frames} queueBuffer calls"


def select_target_process(tp: TraceProcessor, package: str | None = None) -> TargetProcess:
    if package:
        found = rows(tp, _FRAME_SIGNALS.format(where=f"where p.name = {sql_str(package)}"))
        if not found:
            return TargetProcess(available=False, reason=f"process '{package}' not found in trace")
        top = found[0]
        return TargetProcess(
            available=True, reason=f"selected by --package ({_describe(top)})", upid=top.upid, pid=top.pid, name=top.name
        )

    found = rows(tp, _FRAME_SIGNALS.format(
        where=f"where p.name is not null and p.name != {sql_str(SURFACEFLINGER)}"))
    if not found or max(found[0].tl_frames, found[0].qb_frames) == 0:
        return TargetProcess(
            available=False,
            reason="no app frames (FrameTimeline or queueBuffer); pass --package to choose the game process",
        )
    top = found[0]
    runner_up = f"; next: {found[1].name} ({_describe(found[1])})" if len(found) > 1 else ""
    return TargetProcess(
        available=True,
        reason=f"auto: most frames ({_describe(top)}){runner_up}",
        upid=top.upid,
        pid=top.pid,
        name=top.name,
    )
