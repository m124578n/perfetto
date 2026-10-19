"""Deterministic rule baseline.

Thresholds were fixed before looking at any eval results (see README). The
point is a transparent yardstick: if the LLM cannot beat a dozen if-statements,
it is not adding value.
"""

from __future__ import annotations

from dataclasses import dataclass

from gameperf_agent.schema import ThreadMetrics, TraceMetrics
from gameperf_agent.verdict import Evidence, Verdict


@dataclass(frozen=True)
class Thresholds:
    min_frames: int = 300
    problem_jank_rate: float = 0.05
    problem_slow_frame_rate: float = 0.05
    problem_1pct_low_vs_avg: float = 0.5
    fps_drop_late_vs_early: float = 0.85
    temp_rise_c: float = 5.0
    freq_decline_late_vs_early: float = 0.9
    cpu_bound_util: float = 0.80
    sched_runnable_ratio: float = 0.15
    sched_little_share: float = 0.30
    sched_min_util: float = 0.30
    gpu_busy_pct: float = 85.0
    gpu_high_time: float = 0.5
    gpu_cpu_not_busy_util: float = 0.60
    io_d_share: float = 0.10
    sf_jank_share: float = 0.5


T = Thresholds()


class _Collector:
    def __init__(self) -> None:
        self.items: list[Evidence] = []

    def add(self, claim: str, path: str, value) -> None:
        self.items.append(Evidence(claim=claim, metric_path=path, value=value))


def _key_thread(m: TraceMetrics) -> tuple[int, ThreadMetrics] | None:
    threads = m.threads.threads
    ranked = [(i, t) for i, t in enumerate(threads) if t.role in ("game_logic", "render")] or list(enumerate(threads))
    return max(ranked, key=lambda it: it[1].cpu_util) if ranked else None


def _fps_drop(m: TraceMetrics) -> tuple[float, float, float] | None:
    seg = m.frames.segments
    if len(seg) == 3 and seg[0].avg_fps and seg[2].avg_fps:
        return seg[0].avg_fps, seg[2].avg_fps, seg[2].avg_fps / seg[0].avg_fps
    return None


def classify(m: TraceMetrics, t: Thresholds = T) -> Verdict:
    ev = _Collector()
    f = m.frames

    # 1. Enough data to say anything?
    if not f.available:
        ev.add("No frame data, so frame pacing cannot be judged.", "frames.available", False)
        return Verdict(category="insufficient_data", confidence="high", evidence=ev.items,
                       next_steps=["Record with atrace gfx and android.surfaceflinger.frametimeline enabled."])
    if (f.presented_frame_count or 0) < t.min_frames:
        ev.add(f"Only {f.presented_frame_count} presented frames (< {t.min_frames}).",
               "frames.presented_frame_count", f.presented_frame_count)
        return Verdict(category="insufficient_data", confidence="high", evidence=ev.items,
                       next_steps=["Record a longer gameplay session (30-60 s)."])
    if not m.threads.available:
        ev.add("No scheduling data, so CPU work cannot be attributed.", "threads.available", False)
        return Verdict(category="insufficient_data", confidence="medium", evidence=ev.items,
                       next_steps=["Enable ftrace sched/sched_switch."])

    # 2. Is there a performance problem at all?
    problems = []
    if f.jank and f.jank.jank_rate >= t.problem_jank_rate:
        problems.append(("jank", f"Jank rate {f.jank.jank_rate:.3f}.", "frames.jank.jank_rate", f.jank.jank_rate))
    if f.slow_frame_rate is not None and f.slow_frame_rate >= t.problem_slow_frame_rate:
        problems.append(("slow", f"Slow-frame rate {f.slow_frame_rate:.3f}.", "frames.slow_frame_rate", f.slow_frame_rate))
    if f.fps_1pct_low and f.avg_fps and f.fps_1pct_low < t.problem_1pct_low_vs_avg * f.avg_fps:
        problems.append(("lows", f"1% low {f.fps_1pct_low:.1f} fps vs average {f.avg_fps:.1f}.",
                         "frames.fps_1pct_low", f.fps_1pct_low))
    drop = _fps_drop(m)
    if drop and drop[2] <= t.fps_drop_late_vs_early:
        problems.append(("drop", f"FPS fell from {drop[0]:.1f} (early) to {drop[1]:.1f} (late).",
                         "frames.segments.2.avg_fps", drop[1]))
    if not problems:
        ev.add(f"Average {f.avg_fps:.1f} fps with no jank, lows or degradation above thresholds.",
               "frames.avg_fps", f.avg_fps)
        return Verdict(category="no_clear_bottleneck", confidence="medium", evidence=ev.items,
                       next_steps=["Re-test in a heavier scene or after a longer session."])
    for _, claim, path, value in problems:
        ev.add(claim, path, value)

    # 3. Thermal: degradation over time plus a thermal signal.
    if drop and drop[2] <= t.fps_drop_late_vs_early:
        thermal_signals = []
        th = m.thermal
        if th.thermal_status_max:
            thermal_signals.append(("Android thermal status reached "
                                    f"{th.thermal_status_max:.0f}.", "thermal.thermal_status_max", th.thermal_status_max))
        for i, z in enumerate(th.zones):
            if z.delta_c >= t.temp_rise_c:
                thermal_signals.append((f"{z.zone} rose {z.delta_c:.1f} C.", f"thermal.zones.{i}.delta_c", z.delta_c))
        for i, c in enumerate(m.cpufreq.clusters):
            if c.late_vs_early_max_ratio is not None and c.late_vs_early_max_ratio <= t.freq_decline_late_vs_early:
                thermal_signals.append((f"{c.cluster} max frequency fell to {c.late_vs_early_max_ratio:.2f}x of early.",
                                        f"cpufreq.clusters.{i}.late_vs_early_max_ratio", c.late_vs_early_max_ratio))
        if thermal_signals:
            for claim, path, value in thermal_signals:
                ev.add(claim, path, value)
            return Verdict(category="thermal_throttling", confidence="medium" if len(thermal_signals) > 1 else "low",
                           evidence=ev.items,
                           next_steps=["Repeat after cooling the device and compare early-segment FPS.",
                                       "Check whether frequency caps come from thermal mitigation or a game power policy."])

    key = _key_thread(m)
    if key is None:
        return Verdict(category="insufficient_data", confidence="low", evidence=ev.items,
                       next_steps=["No threads found for the target process."])
    ki, kt = key
    base = f"threads.threads.{ki}"

    # 4. CPU-bound: the key thread is nearly always running.
    if kt.cpu_util >= t.cpu_bound_util:
        ev.add(f"{kt.name} ({kt.role}) runs {kt.cpu_util:.0%} of the time.", f"{base}.cpu_util", kt.cpu_util)
        return Verdict(category="cpu_bound_main_thread", confidence="medium", evidence=ev.items,
                       next_steps=[f"Profile {kt.name} with callstack sampling (linux.perf) to find hot functions."])

    # 5. Scheduling / placement: the key thread waits for a CPU or runs on little cores.
    little = kt.cluster_share.get("little", 0.0)
    if (kt.runnable_ratio or 0) >= t.sched_runnable_ratio and kt.cpu_util >= t.sched_min_util:
        ev.add(f"{kt.name} spends {kt.runnable_ratio:.0%} of its active time runnable but not running.",
               f"{base}.runnable_ratio", kt.runnable_ratio)
        return Verdict(category="scheduling_placement", confidence="medium", evidence=ev.items,
                       next_steps=["Inspect sched_waking -> running latency and competing threads on the same CPUs."])
    if little >= t.sched_little_share and kt.cpu_util >= t.sched_min_util:
        ev.add(f"{kt.name} runs {little:.0%} of its time on little cores.", f"{base}.cluster_share.little", little)
        return Verdict(category="scheduling_placement", confidence="medium", evidence=ev.items,
                       next_steps=["Check uclamp / cpuset / task placement hints (e.g. ADPF) for the game threads."])

    # 6. GPU-bound: CPU side idle-ish while the GPU is saturated.
    g = m.gpu
    if kt.cpu_util < t.gpu_cpu_not_busy_util:
        if g.available and ((g.avg_busy_pct or 0) >= t.gpu_busy_pct or (g.time_busy_high_ratio or 0) >= t.gpu_high_time
                            or (g.time_at_hw_max_ratio or 0) >= t.gpu_high_time):
            ev.add(f"{kt.name} runs only {kt.cpu_util:.0%} of the time.", f"{base}.cpu_util", kt.cpu_util)
            if g.avg_busy_pct is not None:
                ev.add(f"GPU busy {g.avg_busy_pct:.0f}% on average.", "gpu.avg_busy_pct", g.avg_busy_pct)
            ev.add(f"GPU at >=95% of max clock {g.time_at_hw_max_ratio:.0%} of the time.",
                   "gpu.time_at_hw_max_ratio", g.time_at_hw_max_ratio)
            return Verdict(category="gpu_bound", confidence="medium", evidence=ev.items,
                           next_steps=["Capture GPU render stages / counters (AGI or vendor profiler)."])

    # 7. I/O or SurfaceFlinger-side problems.
    d_share = kt.uninterruptible_ms / m.threads.window_ms if m.threads.window_ms else 0.0
    if d_share >= t.io_d_share:
        ev.add(f"{kt.name} is blocked in D state {kt.uninterruptible_ms:.0f} ms.", f"{base}.uninterruptible_ms",
               kt.uninterruptible_ms)
        return Verdict(category="io_or_other", confidence="medium", evidence=ev.items,
                       next_steps=["Inspect blocked_function in thread_state and block I/O events."])
    if f.jank and f.jank.jank_frames and f.jank.sf_jank_frames / f.jank.jank_frames >= t.sf_jank_share:
        ev.add("Most jank is attributed to SurfaceFlinger / display.", "frames.jank.sf_jank_frames", f.jank.sf_jank_frames)
        return Verdict(category="io_or_other", confidence="medium", evidence=ev.items,
                       next_steps=["Look at SurfaceFlinger main thread and composition in the same window."])

    # GPU suspected only by elimination (no GPU data or not saturated).
    if kt.cpu_util < t.gpu_cpu_not_busy_util and not g.available:
        ev.add(f"{kt.name} runs only {kt.cpu_util:.0%} of the time and GPU data is unavailable.",
               f"{base}.cpu_util", kt.cpu_util)
        return Verdict(category="gpu_bound", confidence="low", evidence=ev.items,
                       next_steps=["Record GPU frequency/utilisation (sidecar) to confirm."])

    ev.add(f"No rule explains the problem; key thread {kt.name} util {kt.cpu_util:.0%}.", f"{base}.cpu_util", kt.cpu_util)
    return Verdict(category="io_or_other", confidence="low", evidence=ev.items,
                   next_steps=["Inspect the slowest frames individually in the Perfetto UI."])
