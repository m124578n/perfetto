import pytest
from synth import (
    JANK_APP_DEADLINE_MISSED,
    JANK_BUFFER_STUFFING,
    JANK_DROPPED,
    JANK_SF_CPU_DEADLINE_MISSED,
    MS,
    PRESENT_DROPPED,
    PRESENT_LATE,
    SynthTrace,
    steady_frames,
)

from gameperf_agent.metrics.frames import extract_frames
from gameperf_agent.metrics.process import select_target_process

PID = 1234
LAYER = "SurfaceView[com.example.game/UnityPlayerActivity]#0"


def _game() -> SynthTrace:
    return SynthTrace().add_processes({PID: "com.example.game"})


def _frames(tp):
    return extract_frames(tp, select_target_process(tp).upid)


def test_steady_60fps(load):
    t = _game()
    steady_frames(t, pid=PID, layer=LAYER, count=121, interval_ms=1000 / 60)
    f = _frames(load(t))

    assert f.available
    assert f.layer_name == LAYER
    assert f.frame_count == 121
    assert f.presented_frame_count == 121
    assert f.present_time_source == "surfaceflinger_display_frame"
    assert f.avg_fps == pytest.approx(60, abs=0.1)
    assert f.fps_1pct_low == pytest.approx(60, abs=0.1)
    assert f.frame_interval_ms.p50 == pytest.approx(16.67, abs=0.01)
    assert f.jank.jank_frames == 0
    assert f.jank.jank_rate == 0


def test_present_time_comes_from_surfaceflinger_not_app_end(load):
    # App work ends at irregular times, but presents are a clean 20 ms apart.
    t = _game()
    for i, work in enumerate([2, 15, 3, 18, 4, 11]):
        start = 1_000 * MS + i * 20 * MS
        t.add_frame(pid=PID, layer=LAYER, start=start, app_end=start + work * MS, present=start + 25 * MS)
    f = _frames(load(t))
    assert f.frame_interval_ms.max == pytest.approx(20.0)
    assert f.frame_interval_ms.p50 == pytest.approx(20.0)


def test_falls_back_to_app_end_without_surfaceflinger_frames(load):
    t = _game()
    for i in range(10):
        start = 1_000 * MS + i * 20 * MS
        t.add_frame(pid=PID, layer=LAYER, start=start, app_end=start + 5 * MS, present=start + 25 * MS, with_sf=False)
    f = _frames(load(t))
    assert f.present_time_source == "app_frame_end_fallback"
    assert f.frame_interval_ms.p50 == pytest.approx(20.0)


def test_jank_classification(load):
    t = _game()
    t0 = steady_frames(t, pid=PID, layer=LAYER, count=10, interval_ms=16.6)
    kinds = [
        (JANK_APP_DEADLINE_MISSED, PRESENT_LATE),
        (JANK_APP_DEADLINE_MISSED | JANK_BUFFER_STUFFING, PRESENT_LATE),
        (JANK_BUFFER_STUFFING, PRESENT_LATE),
        (JANK_SF_CPU_DEADLINE_MISSED, PRESENT_LATE),
        (JANK_DROPPED, PRESENT_DROPPED),
    ]
    for i, (jank, present_type) in enumerate(kinds):
        start = t0 + i * 17 * MS
        t.add_frame(
            pid=PID, layer=LAYER, start=start, app_end=start + 25 * MS, present=start + 40 * MS,
            jank=jank, present_type=present_type, on_time_finish=False,
        )
    f = _frames(load(t))
    j = f.jank

    assert f.frame_count == 15
    assert f.dropped_frame_count == 1
    assert f.presented_frame_count == 14
    # Perfetto's android_is_missed_frame_type: app + SF + dropped; buffer stuffing alone is not jank.
    assert j.jank_frames == 4
    assert j.app_jank_frames == 2
    assert j.sf_jank_frames == 1
    assert j.dropped_frames == 1
    assert j.buffer_stuffing_frames == 2
    assert j.jank_rate == pytest.approx(4 / 15)
    # Combined jank strings are split; each type counted once per frame.
    assert j.by_type == {
        "App Deadline Missed": 2,
        "Buffer Stuffing": 2,
        "SurfaceFlinger CPU Deadline Missed": 1,
        "Dropped Frame": 1,
    }
    assert f.on_time_finish_rate == pytest.approx(10 / 15)


def test_dropped_frames_excluded_from_intervals(load):
    t = _game()
    for i in range(10):
        start = 1_000 * MS + i * 10 * MS
        dropped = i == 5
        t.add_frame(
            pid=PID, layer=LAYER, start=start, app_end=start + 2 * MS, present=start + 12 * MS,
            jank=JANK_DROPPED if dropped else 1, present_type=PRESENT_DROPPED if dropped else 1,
        )
    f = _frames(load(t))
    # 9 presented frames -> 8 intervals; the one spanning the dropped frame is 20 ms.
    assert f.frame_interval_ms.count == 8
    assert f.frame_interval_ms.max == pytest.approx(20.0)


def test_idle_gaps_are_excluded_and_counted(load):
    t = _game()
    t1 = steady_frames(t, pid=PID, layer=LAYER, count=31, interval_ms=10)
    steady_frames(t, pid=PID, layer=LAYER, count=31, interval_ms=10, t0=t1 + 3_000 * MS)
    f = _frames(load(t))
    assert f.idle_gap_count == 1
    assert f.frame_interval_ms.count == 60
    assert f.avg_fps == pytest.approx(100, abs=0.5)


def test_one_percent_low(load):
    t = _game()
    start = 1_000 * MS
    for i in range(201):
        t.add_frame(pid=PID, layer=LAYER, start=start, app_end=start + 2 * MS, present=start + 5 * MS)
        start += (50 if i in (50, 150) else 10) * MS
    f = _frames(load(t))
    # 200 intervals -> slowest 1% is 2 intervals of 50 ms -> 20 fps.
    assert f.fps_1pct_low == pytest.approx(20.0)
    assert f.frame_interval_ms.p99 > 10


def test_segments_show_fps_degradation(load):
    t = _game()
    t1 = steady_frames(t, pid=PID, layer=LAYER, count=120, interval_ms=1000 / 60)
    t2 = steady_frames(t, pid=PID, layer=LAYER, count=120, interval_ms=1000 / 60, t0=t1)
    steady_frames(t, pid=PID, layer=LAYER, count=60, interval_ms=1000 / 30, t0=t2)
    f = _frames(load(t))
    early, mid, late = f.segments
    assert [s.label for s in f.segments] == ["early", "mid", "late"]
    assert early.avg_fps == pytest.approx(60, abs=1)
    assert late.avg_fps == pytest.approx(30, abs=1)
    # Every frame lands in exactly one segment, including the first (which starts before its present).
    assert sum(s.frame_count for s in f.segments) == f.frame_count
    assert early.start_s == 0 and late.end_s == pytest.approx(f.duration_s)


def test_picks_layer_with_most_frames(load):
    t = _game()
    steady_frames(t, pid=PID, layer=LAYER, count=50, interval_ms=16.6)
    steady_frames(t, pid=PID, layer="UiOverlay#3", count=5, interval_ms=16.6, t0=9_000 * MS)
    f = _frames(load(t))
    assert f.layer_name == LAYER
    assert f.layers == {LAYER: 50, "UiOverlay#3": 5}


def _unity_like(intervals_ms: list[float], pid: int = PID, tid: int = 1241) -> SynthTrace:
    """A game that presents via eglSwapBuffers -> queueBuffer (no FrameTimeline app frames)."""
    t = _game().add_threads(pid, {tid: "UnityGfxDeviceW"})
    sl, s = [], 1_000 * MS
    for iv in [0.0] + intervals_ms:
        s += int(iv * MS)
        # Real traces nest queueBuffer inside queueBuffer; only the outer one is a frame.
        sl += [("eglSwapBuffers", s, s + 2 * MS), ("queueBuffer", s + MS // 2, s + MS),
               ("queueBuffer", s + MS // 2 + 100, s + MS - 100)]
    t.add_atrace_slices(pid, tid, sl)
    return t


def test_falls_back_to_queue_buffer_without_frame_timeline(load):
    f = _frames(load(_unity_like([16.0] * 99 + [50.0])))
    assert f.available
    assert f.frame_source == "queue_buffer"
    assert f.layer_name == "thread:UnityGfxDeviceW"
    assert f.frame_count == 101
    assert f.frame_interval_ms.count == 100
    assert f.frame_interval_ms.p50 == pytest.approx(16.0)
    assert f.fps_1pct_low == pytest.approx(20.0)
    # No jank classification without FrameTimeline; slow-frame heuristic still applies.
    assert f.jank is None
    assert f.slow_frame_count == 1
    assert f.slow_frame_rate == pytest.approx(0.01)


def test_slow_frames_with_frame_timeline(load):
    t = _game()
    t0 = steady_frames(t, pid=PID, layer=LAYER, count=50, interval_ms=10)
    steady_frames(t, pid=PID, layer=LAYER, count=1, interval_ms=10, t0=t0 + 20 * MS)
    f = _frames(load(t))
    assert f.frame_source == "frame_timeline"
    assert f.slow_frame_count == 1  # the 30 ms gap is >= 1.5x the 10 ms median


def test_no_frame_timeline(load):
    tp = load(_game())
    f = extract_frames(tp, upid=None)
    assert not f.available
    assert f.avg_fps is None
    assert f.reason
