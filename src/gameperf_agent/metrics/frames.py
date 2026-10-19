"""Frame pacing and jank from the Android FrameTimeline (Android 12+).

Present time of an app frame = end of the SurfaceFlinger *display* frame that
shares its display_frame_token. The app frame's own end is max(GPU done, post
time), which jitters by several ms and is not when the frame reached the
screen. Verified on a real trace: SF-based intervals are clean vsync multiples,
app-end-based ones are not.

Fallback: games that present via eglSwapBuffers/vkQueuePresentKHR without
Choreographer frame tokens (verified: a Unity GLES title) have no app frames
in FrameTimeline. Their frames are then the outermost atrace `queueBuffer`
slices on the producing thread (needs atrace category gfx). Those times are
when the game handed a buffer over, not when it reached the screen, and carry
no jank classification.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._util import interval_stats, rows
from gameperf_agent.schema import FrameMetrics, FrameSegment, JankStats

# Intervals longer than this are the game being idle/paused, not slow frames.
IDLE_GAP_MS = 1000.0
# Heuristic available for every source: an interval this many times the median is "slow".
SLOW_FRAME_FACTOR = 1.5
SEGMENT_LABELS = ("early", "mid", "late")
DROPPED = "Dropped Frame"

_FRAMES_SQL = """
include perfetto module android.frames.jank_type;
select
  a.ts,
  a.dur,
  a.layer_name,
  a.jank_type,
  a.present_type,
  a.on_time_finish,
  coalesce(android_is_app_jank_type(a.jank_type), 0) as is_app,
  coalesce(android_is_sf_jank_type(a.jank_type), 0) as is_sf,
  coalesce(android_is_missed_frame_type(a.jank_type), 0) as is_missed,
  (select max(s.ts + s.dur) from actual_frame_timeline_slice s
   where s.display_frame_token = a.display_frame_token and s.layer_name is null) as sf_present_ts
from actual_frame_timeline_slice a
where a.upid = {upid} and a.layer_name is not null
order by a.ts
"""

_QUEUE_BUFFER_SQL = """
select t.utid, t.name as thread_name, s.ts, s.dur
from slice s join thread_track tt on s.track_id = tt.id join thread t using (utid)
where t.upid = {upid} and s.name = 'queueBuffer'
  and (s.parent_id is null or (select p.name from slice p where p.id = s.parent_id) != 'queueBuffer')
order by s.ts
"""


@dataclass
class _Frame:
    ts: int
    jank_types: list[str]
    present_type: str | None
    on_time_finish: bool | None
    is_app: bool
    is_sf: bool
    is_missed: bool
    present_ts: int

    @property
    def dropped(self) -> bool:
        return self.present_type == DROPPED or DROPPED in self.jank_types


def _unavailable(reason: str) -> FrameMetrics:
    return FrameMetrics(available=False, reason=reason)


def extract_frames(tp: TraceProcessor, upid: int | None) -> FrameMetrics:
    if upid is None:
        return _unavailable("no target process")

    raw = rows(tp, _FRAMES_SQL.format(upid=int(upid)))
    if not raw:
        return _from_queue_buffer(tp, upid)

    layers = Counter(r.layer_name for r in raw)
    layer, _ = layers.most_common(1)[0]
    layer_rows = [r for r in raw if r.layer_name == layer]

    has_sf = any(r.sf_present_ts is not None for r in layer_rows)
    frames = [
        _Frame(
            ts=r.ts,
            jank_types=[t.strip() for t in r.jank_type.split(",")] if r.jank_type else [],
            present_type=r.present_type,
            on_time_finish=None if r.on_time_finish is None else bool(r.on_time_finish),
            is_app=bool(r.is_app),
            is_sf=bool(r.is_sf),
            is_missed=bool(r.is_missed),
            present_ts=(r.sf_present_ts if has_sf else r.ts + r.dur),
        )
        for r in layer_rows
    ]

    return _build(
        frames,
        frame_source="frame_timeline",
        layer=layer,
        layers=dict(layers.most_common()),
        present_time_source="surfaceflinger_display_frame" if has_sf else "app_frame_end_fallback",
        with_jank=True,
    )


def _from_queue_buffer(tp: TraceProcessor, upid: int) -> FrameMetrics:
    raw = rows(tp, _QUEUE_BUFFER_SQL.format(upid=int(upid)))
    if not raw:
        return _unavailable(
            "no frames for target process: no FrameTimeline app frames (needs Android 12+ and "
            "android.surfaceflinger.frametimeline) and no atrace queueBuffer slices (needs atrace gfx)"
        )
    per_thread = Counter((r.utid, r.thread_name) for r in raw)
    (utid, name), _ = per_thread.most_common(1)[0]
    frames = [
        _Frame(ts=r.ts, jank_types=[], present_type=None, on_time_finish=None,
               is_app=False, is_sf=False, is_missed=False, present_ts=r.ts + r.dur)
        for r in raw if r.utid == utid
    ]
    return _build(
        frames,
        frame_source="queue_buffer",
        layer=f"thread:{name}",
        layers={f"thread:{n}": c for (_, n), c in per_thread.most_common()},
        present_time_source="app_queue_buffer",
        with_jank=False,
    )


def _build(frames: list[_Frame], *, frame_source: str, layer: str, layers: dict[str, int],
           present_time_source: str, with_jank: bool) -> FrameMetrics:
    presented = sorted((f for f in frames if not f.dropped and f.present_ts is not None), key=lambda f: f.present_ts)
    present_times = [f.present_ts for f in presented]
    gaps = [(b - a) / 1e6 for a, b in zip(present_times, present_times[1:])]
    intervals = [g for g in gaps if g <= IDLE_GAP_MS]
    stats = interval_stats(intervals)
    slow = sum(i >= SLOW_FRAME_FACTOR * stats.p50 for i in intervals) if stats.p50 else 0

    return FrameMetrics(
        available=True,
        frame_source=frame_source,
        layer_name=layer,
        layers=layers,
        present_time_source=present_time_source,
        frame_count=len(frames),
        presented_frame_count=len(presented),
        dropped_frame_count=sum(f.dropped for f in frames) if with_jank else None,
        duration_s=(present_times[-1] - present_times[0]) / 1e9 if len(present_times) > 1 else None,
        avg_fps=1000.0 / stats.mean if stats.mean else None,
        fps_1pct_low=_one_percent_low(intervals),
        frame_interval_ms=stats,
        idle_gap_count=len(gaps) - len(intervals),
        slow_frame_count=slow,
        slow_frame_rate=slow / len(intervals) if intervals else None,
        jank=_jank(frames) if with_jank else None,
        present_type_counts=dict(Counter(f.present_type for f in frames if f.present_type).most_common()),
        on_time_finish_rate=_rate([f.on_time_finish for f in frames if f.on_time_finish is not None]),
        segments=_segments(frames, presented, with_jank),
    )


def _one_percent_low(intervals: list[float]) -> float | None:
    """FPS equivalent of the mean of the slowest 1% of frame intervals (at least one)."""
    if not intervals:
        return None
    slowest = sorted(intervals, reverse=True)[: max(1, math.ceil(len(intervals) * 0.01))]
    return 1000.0 / (sum(slowest) / len(slowest))


def _jank(frames: list[_Frame]) -> JankStats:
    by_type: Counter[str] = Counter()
    for f in frames:
        by_type.update(t for t in set(f.jank_types) if t != "None")
    jank_frames = sum(f.is_missed for f in frames)
    return JankStats(
        jank_frames=jank_frames,
        jank_rate=jank_frames / len(frames),
        app_jank_frames=sum(f.is_app for f in frames),
        sf_jank_frames=sum(f.is_sf for f in frames),
        dropped_frames=sum(f.dropped for f in frames),
        buffer_stuffing_frames=sum("Buffer Stuffing" in f.jank_types for f in frames),
        by_type=dict(by_type.most_common()),
    )


def _rate(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def _segments(frames: list[_Frame], presented: list[_Frame], with_jank: bool) -> list[FrameSegment]:
    """Splits the presented time span into equal thirds (for thermal/degradation trends).

    An interval belongs to the segment containing its ending present; a frame's
    jank belongs to the segment containing its start.
    """
    if len(presented) < 2:
        return []
    t0, t_end = presented[0].present_ts, presented[-1].present_ts
    width = (t_end - t0) / len(SEGMENT_LABELS)
    out = []
    for i, label in enumerate(SEGMENT_LABELS):
        seg_lo, seg_hi = t0 + i * width, t0 + (i + 1) * width
        lo, hi = seg_lo, seg_hi
        # Outer segments are open-ended so frames starting before the first
        # present (or after the last) are still counted.
        if i == 0:
            lo = -math.inf
        if i == len(SEGMENT_LABELS) - 1:
            hi = math.inf
        in_seg = lambda t, lo=lo, hi=hi: lo <= t < hi  # noqa: E731
        seg_intervals = [
            (b.present_ts - a.present_ts) / 1e6
            for a, b in zip(presented, presented[1:])
            if in_seg(b.present_ts) and (b.present_ts - a.present_ts) / 1e6 <= IDLE_GAP_MS
        ]
        seg_frames = [f for f in frames if in_seg(f.ts)]
        out.append(
            FrameSegment(
                label=label,
                start_s=(seg_lo - t0) / 1e9,
                end_s=(seg_hi - t0) / 1e9,
                frame_count=len(seg_frames),
                avg_fps=1000.0 * len(seg_intervals) / sum(seg_intervals) if seg_intervals else None,
                jank_rate=_rate([f.is_missed for f in seg_frames]) if with_jank else None,
            )
        )
    return out
