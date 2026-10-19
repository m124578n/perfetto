"""Structured metric models.

Every block carries `available` + `reason`. When data is missing, numeric fields
stay None (never 0) so downstream consumers cannot mistake "absent" for "zero".
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TargetProcess(BaseModel):
    available: bool
    reason: str
    upid: int | None = None
    pid: int | None = None
    name: str | None = None


class IntervalStats(BaseModel):
    count: int
    mean: float | None = None
    p50: float | None = None
    p90: float | None = None
    p99: float | None = None
    max: float | None = None


class JankStats(BaseModel):
    jank_frames: int = Field(description="Frames Perfetto classifies as missed: app, SurfaceFlinger or dropped.")
    jank_rate: float = Field(description="jank_frames / frame_count")
    app_jank_frames: int
    sf_jank_frames: int
    dropped_frames: int
    buffer_stuffing_frames: int = Field(description="Latency, not a missed frame; reported separately.")
    by_type: dict[str, int] = Field(description="Per jank type; a frame with combined types counts once per type.")


class FrameSegment(BaseModel):
    label: str
    start_s: float = Field(description="Seconds from the first presented frame.")
    end_s: float
    frame_count: int
    avg_fps: float | None = None
    jank_rate: float | None = None


class CpuCluster(BaseModel):
    name: str = Field(description="Rank-based label: little < mid < big < prime.")
    rank: int
    cpus: list[int]
    capacity: int | None = None
    max_freq_khz: int | None = None


class CpuTopology(BaseModel):
    available: bool
    reason: str | None = None
    ordering_basis: str | None = Field(
        default=None, description="capacity | max_available_freq | observed_max_freq | cpu_index"
    )
    clusters: list[CpuCluster] = Field(default_factory=list)

    def cluster_name_by_cpu(self) -> dict[int, str]:
        return {cpu: c.name for c in self.clusters for cpu in c.cpus}


class ThreadMetrics(BaseModel):
    utid: int
    tid: int
    name: str | None
    role: str = Field(description="game_logic | render | main | top_cpu")
    identified_by: str = Field(description="name | main_thread | top_cpu")
    running_ms: float
    runnable_ms: float = Field(description="R + R+: ready to run but waiting for a CPU.")
    uninterruptible_ms: float = Field(description="D states: usually blocked on I/O or a kernel lock.")
    sleeping_ms: float
    cpu_util: float = Field(description="running_ms / window_ms")
    runnable_ratio: float | None = Field(description="runnable / (running + runnable)")
    cluster_share: dict[str, float] = Field(description="Share of running time per cluster name.")


class ThreadsMetrics(BaseModel):
    available: bool
    reason: str | None = None
    window_ms: float | None = None
    threads: list[ThreadMetrics] = Field(default_factory=list)
    process_running_ms: float | None = None
    process_cluster_share: dict[str, float] = Field(default_factory=dict)


class SeriesSegment(BaseModel):
    label: str
    avg: float | None = None
    max: float | None = None


class ClusterFreq(BaseModel):
    cluster: str
    cpus: list[int]
    hw_max_mhz: float
    min_mhz: float
    max_mhz: float
    avg_mhz: float | None
    avg_vs_hw_max: float | None
    time_at_hw_max_ratio: float | None = Field(description="Time share at >= 95% of hardware max.")
    late_vs_early_max_ratio: float | None = Field(description="Max freq in last third / first third; < 0.9 hints at a cap.")
    segments: list[SeriesSegment]


class CpuFreqMetrics(BaseModel):
    available: bool
    reason: str | None = None
    clusters: list[ClusterFreq] = Field(default_factory=list)


class GpuMetrics(BaseModel):
    available: bool
    reason: str | None = None
    source: str | None = Field(default=None, description="trace | sidecar")
    hw_max_mhz: float | None = None
    min_mhz: float | None = None
    max_mhz: float | None = None
    avg_mhz: float | None = None
    time_at_hw_max_ratio: float | None = Field(default=None, description="Time share at >= 95% of hw max.")
    avg_busy_pct: float | None = None
    time_busy_high_ratio: float | None = Field(default=None, description="Time share with GPU busy >= 90%.")
    cap_min_mhz: float | None = Field(default=None, description="Lowest dynamic GPU max-clock limit (sidecar).")
    capped_time_ratio: float | None = Field(default=None, description="Time share with the GPU cap < 95% of hw max.")
    thermal_pwrlevel_max: float | None = Field(default=None, description="KGSL thermal power level; 0 = no thermal limit.")
    segments: list[SeriesSegment] = Field(default_factory=list)


class ZoneTemp(BaseModel):
    zone: str = Field(description="Zone group; members like gpuss-0..7 merged by max.")
    source: str
    start_c: float
    end_c: float
    max_c: float
    delta_c: float
    slope_c_per_min: float | None


class CpuCap(BaseModel):
    cluster: str
    policy: str
    hw_max_mhz: float
    min_cap_mhz: float
    min_cap_ratio: float = Field(description="Lowest scaling_max_freq / hw max.")
    capped_time_ratio: float | None = Field(description="Time share with scaling_max < 95% of hw max.")


class ThermalMetrics(BaseModel):
    available: bool
    reason: str | None = None
    zones: list[ZoneTemp] = Field(default_factory=list)
    thermal_status_max: float | None = Field(default=None, description="Android thermal status 0-6 (0 = none).")
    thermal_status_ge_light_ratio: float | None = None
    thermal_status_ge_severe_ratio: float | None = None
    cpu_caps: list[CpuCap] = Field(default_factory=list)


class SidecarAlignment(BaseModel):
    available: bool
    reason: str | None = None
    compared_samples: int | None = None
    match_ratio: float | None = Field(
        default=None, description="Sidecar cur freq seen in trace cpufreq within 50 ms of its timestamp."
    )
    shifted_baseline_ratio: float | None = Field(
        default=None, description="Same score with the sidecar shifted +/-1-2 s; aligned if match_ratio is well above."
    )


class MetaMetrics(BaseModel):
    window_s: float = Field(description="Analysis window (span of scheduling data).")
    android_sdk: int | None = None
    build_fingerprint: str | None = None
    data_sources: dict[str, bool]
    ftrace_setup_errors: list[str] = Field(default_factory=list)


class FrameMetrics(BaseModel):
    available: bool
    reason: str | None = None
    frame_source: str | None = Field(
        default=None,
        description="frame_timeline (jank classified by SurfaceFlinger) | queue_buffer (atrace fallback, no jank types)",
    )
    layer_name: str | None = None
    layers: dict[str, int] = Field(default_factory=dict)
    present_time_source: str | None = None
    frame_count: int | None = None
    presented_frame_count: int | None = None
    dropped_frame_count: int | None = None
    duration_s: float | None = None
    avg_fps: float | None = None
    fps_1pct_low: float | None = None
    frame_interval_ms: IntervalStats | None = None
    idle_gap_count: int | None = None
    slow_frame_count: int | None = Field(default=None, description="Intervals >= 1.5x the median interval.")
    slow_frame_rate: float | None = Field(default=None, description="slow_frame_count / interval count")
    jank: JankStats | None = None
    present_type_counts: dict[str, int] = Field(default_factory=dict)
    on_time_finish_rate: float | None = None
    segments: list[FrameSegment] = Field(default_factory=list)


SCHEMA_VERSION = 1


class TraceMetrics(BaseModel):
    """Everything the interpreter (rules or LLM) is allowed to see."""

    schema_version: int = SCHEMA_VERSION
    trace_file: str
    meta: MetaMetrics
    target: TargetProcess
    topology: CpuTopology
    frames: FrameMetrics
    threads: ThreadsMetrics
    cpufreq: CpuFreqMetrics
    gpu: GpuMetrics
    thermal: ThermalMetrics
    sidecar_alignment: SidecarAlignment
