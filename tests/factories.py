"""Hand-built TraceMetrics for testing interpreters without a trace."""

from __future__ import annotations

from gameperf_agent.schema import (
    ClusterFreq,
    CpuCluster,
    CpuFreqMetrics,
    CpuTopology,
    FrameMetrics,
    FrameSegment,
    GpuMetrics,
    IntervalStats,
    JankStats,
    MetaMetrics,
    SeriesSegment,
    SidecarAlignment,
    TargetProcess,
    ThermalMetrics,
    ThreadMetrics,
    ThreadsMetrics,
    TraceMetrics,
    ZoneTemp,
)


def thread(name="UnityMain", role="game_logic", util=0.5, runnable_ratio=0.05, little=0.0, d_ms=0.0) -> ThreadMetrics:
    running = util * 30_000
    share = {"little": little, "big": 1 - little} if little else {"big": 1.0}
    return ThreadMetrics(
        utid=1, tid=100, name=name, role=role, identified_by="name", running_ms=running,
        runnable_ms=running * runnable_ratio / (1 - runnable_ratio), uninterruptible_ms=d_ms, sleeping_ms=0.0,
        cpu_util=util, runnable_ratio=runnable_ratio, cluster_share=share,
    )


def healthy(**kw) -> TraceMetrics:
    """60 fps, no jank, moderate CPU, idle-ish GPU, cool device."""
    seg = [FrameSegment(label=lbl, start_s=i * 10, end_s=(i + 1) * 10, frame_count=600, avg_fps=60, jank_rate=0.0)
           for i, lbl in enumerate(("early", "mid", "late"))]
    m = TraceMetrics(
        trace_file="synthetic",
        meta=MetaMetrics(window_s=30, android_sdk=35, data_sources={"sched": True, "frame_timeline": True}),
        target=TargetProcess(available=True, reason="test", upid=1, pid=100, name="com.example.game"),
        topology=CpuTopology(available=True, ordering_basis="capacity", clusters=[
            CpuCluster(name="little", rank=0, cpus=[0, 1]), CpuCluster(name="big", rank=1, cpus=[2, 3])]),
        frames=FrameMetrics(
            available=True, frame_source="frame_timeline", layer_name="SurfaceView", frame_count=1800,
            presented_frame_count=1800, dropped_frame_count=0, duration_s=30, avg_fps=60, fps_1pct_low=55,
            frame_interval_ms=IntervalStats(count=1799, mean=16.7, p50=16.7, p90=17, p99=18, max=20),
            idle_gap_count=0, slow_frame_count=0, slow_frame_rate=0.0,
            jank=JankStats(jank_frames=0, jank_rate=0.0, app_jank_frames=0, sf_jank_frames=0, dropped_frames=0,
                           buffer_stuffing_frames=0, by_type={}),
            segments=seg,
        ),
        threads=ThreadsMetrics(available=True, window_ms=30_000, threads=[thread()], process_running_ms=15_000,
                               process_cluster_share={"big": 1.0}),
        cpufreq=CpuFreqMetrics(available=True, clusters=[
            ClusterFreq(cluster="big", cpus=[2, 3], hw_max_mhz=3000, min_mhz=1000, max_mhz=3000, avg_mhz=2000,
                        avg_vs_hw_max=0.67, time_at_hw_max_ratio=0.2, late_vs_early_max_ratio=1.0,
                        segments=[SeriesSegment(label="early", max=3000), SeriesSegment(label="mid", max=3000),
                                  SeriesSegment(label="late", max=3000)])]),
        gpu=GpuMetrics(available=True, source="sidecar", hw_max_mhz=1000, avg_mhz=400, max_mhz=600,
                       time_at_hw_max_ratio=0.0, avg_busy_pct=40, time_busy_high_ratio=0.0),
        thermal=ThermalMetrics(available=True, zones=[
            ZoneTemp(zone="cpuss", source="sidecar", start_c=40, end_c=41, max_c=41, delta_c=1, slope_c_per_min=2)],
            thermal_status_max=0, thermal_status_ge_light_ratio=0, thermal_status_ge_severe_ratio=0),
        sidecar_alignment=SidecarAlignment(available=False, reason="test"),
    )
    return m.model_copy(update=kw)


def with_jank(m: TraceMetrics, rate=0.12, sf=0) -> TraceMetrics:
    n = int(m.frames.frame_count * rate)
    jank = JankStats(jank_frames=n, jank_rate=rate, app_jank_frames=n - sf, sf_jank_frames=sf, dropped_frames=0,
                     buffer_stuffing_frames=0, by_type={"App Deadline Missed": n - sf})
    return m.model_copy(update={"frames": m.frames.model_copy(update={"jank": jank, "fps_1pct_low": 30})})


def with_fps_drop(m: TraceMetrics, late=45.0) -> TraceMetrics:
    seg = [s.model_copy() for s in m.frames.segments]
    seg[2] = seg[2].model_copy(update={"avg_fps": late})
    return m.model_copy(update={"frames": m.frames.model_copy(update={"segments": seg})})
