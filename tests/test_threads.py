import pytest
from synth import MS, SynthTrace

from gameperf_agent.metrics.cpu_topology import extract_topology
from gameperf_agent.metrics.process import select_target_process
from gameperf_agent.metrics.threads import extract_threads

PID = 1234
MAIN, UNITY_MAIN, GFX, WORKER, IDLE_WORKER = 1234, 1240, 1241, 1250, 1251
RUN, SLEEP, IO = 0, 1, 2  # sched_switch prev_state
S24 = [(2, 0, [300000, 2265600]), (3, 0, [400000, 3148800]), (2, 0, [400000, 2956800]), (1, 0, [500000, 3398400])]


def _trace() -> SynthTrace:
    t = SynthTrace().add_processes({PID: "com.example.game"}).add_cpus(S24)
    t.add_threads(PID, {MAIN: "example.game", UNITY_MAIN: "UnityMain", GFX: "UnityGfxDeviceW",
                        WORKER: "Job.Worker 0", IDLE_WORKER: "Job.Worker 1"})
    # Window is [1000 ms, 1100 ms] (set by the first and last sched events).
    # UnityMain: 60 ms on prime (cpu7), preempted, waits 20 ms, 10 ms on little (cpu0), then sleeps.
    t.add_runs(7, [(UNITY_MAIN, 1000 * MS, 1060 * MS, RUN)])
    t.add_runs(0, [(UNITY_MAIN, 1080 * MS, 1090 * MS, SLEEP)])
    # Gfx thread: 20 ms on big (cpu2), blocked in D for 30 ms, 10 ms on big, sleeps.
    t.add_runs(2, [(GFX, 1000 * MS, 1020 * MS, IO), (GFX, 1050 * MS, 1060 * MS, SLEEP)])
    # Unnamed busy worker: 40 ms on mid (cpu5). Tiny idle worker: 1 ms.
    t.add_runs(5, [(WORKER, 1000 * MS, 1040 * MS, SLEEP), (IDLE_WORKER, 1050 * MS, 1051 * MS, SLEEP)])
    # Main (Java) thread: 5 ms on little (cpu1); last event fixes the window end.
    t.add_runs(1, [(MAIN, 1000 * MS, 1005 * MS, SLEEP), (MAIN, 1099 * MS, 1100 * MS, SLEEP)])
    return t


def _extract(tp, **kw):
    target = select_target_process(tp, package="com.example.game")
    return extract_threads(tp, target.upid, extract_topology(tp), **kw)


def test_identifies_key_threads_by_name_and_main(load):
    m = _extract(load(_trace()))
    assert m.available
    by_tid = {t.tid: t for t in m.threads}
    assert by_tid[UNITY_MAIN].role == "game_logic" and by_tid[UNITY_MAIN].identified_by == "name"
    assert by_tid[GFX].role == "render"
    assert by_tid[MAIN].role == "main" and by_tid[MAIN].identified_by == "main_thread"
    # Busy unnamed worker is included as a top CPU consumer; ordering is by running time.
    assert by_tid[WORKER].role == "top_cpu"
    # A thread using <5% of the window is noise, not a key thread.
    assert IDLE_WORKER not in by_tid
    assert m.threads[0].tid == UNITY_MAIN


def test_idle_threads_that_inherited_a_key_name_are_dropped(load):
    # Linux threads inherit their creator's name: a real Unity trace had 20 idle "UnityMain"s.
    t = _trace()
    t.add_threads(PID, {1300: "UnityMain", 1301: "UnityMain"})
    t.add_runs(3, [(1300, 1000 * MS, 1001 * MS, SLEEP), (1301, 1002 * MS, 1003 * MS, SLEEP)])
    m = _extract(load(t))
    unity_mains = [th for th in m.threads if th.name == "UnityMain"]
    assert [th.tid for th in unity_mains] == [UNITY_MAIN]


def test_state_breakdown_and_cluster_share(load):
    m = _extract(load(_trace()))
    um = next(t for t in m.threads if t.tid == UNITY_MAIN)
    assert m.window_ms == pytest.approx(100)
    assert um.running_ms == pytest.approx(70)
    assert um.runnable_ms == pytest.approx(20)
    assert um.cpu_util == pytest.approx(0.70)
    assert um.runnable_ratio == pytest.approx(20 / 90)
    assert um.cluster_share == pytest.approx({"prime": 60 / 70, "little": 10 / 70})

    gfx = next(t for t in m.threads if t.tid == GFX)
    assert gfx.uninterruptible_ms == pytest.approx(30)
    assert gfx.cluster_share == pytest.approx({"big": 1.0})


def test_open_ended_states_are_clipped_to_window(load):
    m = _extract(load(_trace()))
    um = next(t for t in m.threads if t.tid == UNITY_MAIN)
    # Sleeps from 1090 ms until trace end (dur = -1) -> clipped to 10 ms.
    assert um.sleeping_ms == pytest.approx(10)


def test_process_level_cluster_share(load):
    m = _extract(load(_trace()))
    # Running: prime 60, little 10+5+1, big 30, mid 41 -> total 147
    assert m.process_running_ms == pytest.approx(147)
    assert m.process_cluster_share["prime"] == pytest.approx(60 / 147)
    assert m.process_cluster_share["mid"] == pytest.approx(41 / 147)


def test_explicit_window(load):
    m = _extract(load(_trace()), window=(1000 * MS, 1050 * MS))
    um = next(t for t in m.threads if t.tid == UNITY_MAIN)
    assert m.window_ms == pytest.approx(50)
    assert um.running_ms == pytest.approx(50)


def test_no_sched_data(load):
    t = SynthTrace().add_processes({PID: "com.example.game"})
    tp = load(t)
    m = extract_threads(tp, select_target_process(tp, package="com.example.game").upid, extract_topology(tp))
    assert not m.available
    assert m.threads == []
