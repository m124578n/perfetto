import pytest
from factories import healthy, thread, with_fps_drop, with_jank

from gameperf_agent import grounding
from gameperf_agent.rules import classify
from gameperf_agent.schema import FrameMetrics, GpuMetrics, ThreadsMetrics, ZoneTemp


def _threads(m, *threads):
    return m.model_copy(update={"threads": m.threads.model_copy(update={"threads": list(threads)})})


def test_healthy_trace_has_no_clear_bottleneck():
    assert classify(healthy()).category == "no_clear_bottleneck"


def test_no_frames_is_insufficient():
    m = healthy(frames=FrameMetrics(available=False, reason="none"))
    assert classify(m).category == "insufficient_data"


def test_too_few_frames_is_insufficient():
    m = healthy()
    m = m.model_copy(update={"frames": m.frames.model_copy(update={"presented_frame_count": 120})})
    assert classify(m).category == "insufficient_data"


def test_no_sched_is_insufficient():
    assert classify(with_jank(healthy(threads=ThreadsMetrics(available=False)))).category == "insufficient_data"


def test_busy_main_thread_is_cpu_bound():
    m = _threads(with_jank(healthy()), thread(util=0.92))
    v = classify(m)
    assert v.category == "cpu_bound_main_thread"
    assert any(e.metric_path == "threads.threads.0.cpu_util" for e in v.evidence)


def test_runnable_key_thread_is_scheduling():
    m = _threads(with_jank(healthy()), thread(util=0.5, runnable_ratio=0.3))
    assert classify(m).category == "scheduling_placement"


def test_key_thread_on_little_cores_is_scheduling():
    m = _threads(with_jank(healthy()), thread(util=0.5, little=0.6))
    assert classify(m).category == "scheduling_placement"


def test_saturated_gpu_with_idle_cpu_is_gpu_bound():
    m = with_jank(healthy(gpu=GpuMetrics(available=True, source="sidecar", hw_max_mhz=1000, avg_mhz=950,
                                         max_mhz=1000, time_at_hw_max_ratio=0.8, avg_busy_pct=97,
                                         time_busy_high_ratio=0.9)))
    assert classify(_threads(m, thread(util=0.35))).category == "gpu_bound"


def test_gpu_suspected_without_gpu_data_is_low_confidence():
    m = _threads(with_jank(healthy(gpu=GpuMetrics(available=False, reason="none"))), thread(util=0.35))
    v = classify(m)
    assert v.category == "gpu_bound" and v.confidence == "low"


def test_fps_drop_with_heating_is_thermal():
    m = with_fps_drop(healthy())
    m = m.model_copy(update={"thermal": m.thermal.model_copy(update={
        "thermal_status_max": 2.0,
        "zones": [ZoneTemp(zone="cpuss", source="sidecar", start_c=40, end_c=52, max_c=52, delta_c=12,
                           slope_c_per_min=24)]})})
    v = classify(m)
    assert v.category == "thermal_throttling"
    assert v.confidence == "medium"


def test_fps_drop_without_thermal_signal_is_not_thermal():
    m = _threads(with_fps_drop(healthy()), thread(util=0.9))
    assert classify(m).category == "cpu_bound_main_thread"


def test_blocked_key_thread_is_io():
    m = _threads(with_jank(healthy()), thread(util=0.4, d_ms=6_000))
    assert classify(m).category == "io_or_other"


def test_surfaceflinger_jank_is_other():
    m = _threads(with_jank(healthy(), sf=150), thread(util=0.7))
    assert classify(m).category == "io_or_other"


@pytest.mark.parametrize("build", [
    lambda: healthy(),
    lambda: _threads(with_jank(healthy()), thread(util=0.92)),
    lambda: _threads(with_jank(healthy()), thread(util=0.5, runnable_ratio=0.3)),
    lambda: _threads(with_jank(healthy()), thread(util=0.4, d_ms=6_000)),
    lambda: with_fps_drop(healthy()).model_copy(update={"thermal": healthy().thermal.model_copy(
        update={"thermal_status_max": 3.0})}),
])
def test_rule_evidence_is_always_grounded(build):
    m = build()
    report = grounding.check(classify(m), m)
    assert report.checks and report.ratio == 1.0, report
