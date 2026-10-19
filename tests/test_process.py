from synth import MS, SynthTrace, steady_frames

from gameperf_agent.metrics.process import select_target_process

GAME_PID = 1234
UI_PID = 2000


def _two_apps() -> SynthTrace:
    t = SynthTrace().add_processes({GAME_PID: "com.example.game", UI_PID: "com.android.systemui"})
    steady_frames(t, pid=GAME_PID, layer="SurfaceView[game]#0", count=30, interval_ms=16.6)
    steady_frames(t, pid=UI_PID, layer="StatusBar#1", count=5, interval_ms=16.6, t0=5_000 * MS)
    return t


def test_auto_selects_process_with_most_frames(load):
    target = select_target_process(load(_two_apps()))
    assert target.available
    assert target.name == "com.example.game"
    assert target.pid == GAME_PID
    assert "most frames" in target.reason


def test_explicit_package(load):
    target = select_target_process(load(_two_apps()), package="com.android.systemui")
    assert target.available
    assert target.pid == UI_PID


def test_unknown_package_is_unavailable(load):
    target = select_target_process(load(_two_apps()), package="com.not.there")
    assert not target.available
    assert target.upid is None
    assert "com.not.there" in target.reason


def test_auto_selects_queue_buffer_producer_over_few_timeline_frames(load):
    # Unity game: no FrameTimeline app frames, many queueBuffer calls. SystemUI: 3 timeline frames.
    t = SynthTrace().add_processes({GAME_PID: "com.example.game", UI_PID: "com.android.systemui"})
    t.add_threads(GAME_PID, {1241: "UnityGfxDeviceW"})
    steady_frames(t, pid=UI_PID, layer="StatusBar#1", count=3, interval_ms=16.6)
    t.add_atrace_slices(GAME_PID, 1241, [("queueBuffer", (2_000 + i * 16) * MS, (2_001 + i * 16) * MS) for i in range(20)])
    target = select_target_process(load(t))
    assert target.name == "com.example.game"
    assert "queueBuffer" in target.reason


def test_no_frames_and_no_package_is_unavailable(load):
    target = select_target_process(load(SynthTrace().add_processes({GAME_PID: "com.example.game"})))
    assert not target.available
    assert target.upid is None
