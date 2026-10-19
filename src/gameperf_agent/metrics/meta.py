"""Trace provenance and data-source availability."""

from __future__ import annotations

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics._util import rows
from gameperf_agent.schema import MetaMetrics

_PRESENCE = {
    "frame_timeline": "select 1 from actual_frame_timeline_slice limit 1",
    "sched": "select 1 from sched limit 1",
    "cpufreq": "select 1 from cpu_counter_track where name = 'cpufreq' limit 1",
    "gpufreq": "select 1 from gpu_counter_track where name = 'gpufreq' limit 1",
    "thermal": (
        "select 1 from counter_track where type in "
        "('thermal_temperature', 'thermal_temperature_sys', 'cooling_device_counter') limit 1"
    ),
}


def analysis_window(tp: TraceProcessor) -> tuple[int, int]:
    """Span of scheduling data, else trace bounds.

    trace_bounds alone can include stale packets from before the recording
    (seen on a real S24 trace: a 10 s recording reported as 47 s).
    """
    r = rows(tp, "select min(ts) as s, max(ts + max(dur, 0)) as e from sched")[0]
    if r.s is not None and r.e > r.s:
        return r.s, r.e
    b = rows(tp, "select start_ts as s, end_ts as e from trace_bounds")[0]
    return b.s, b.e


def extract_meta(tp: TraceProcessor, window: tuple[int, int], has_sidecar: bool) -> MetaMetrics:
    md = {r.name: (r.str_value if r.str_value is not None else r.int_value)
          for r in rows(tp, "select name, str_value, int_value from metadata")}
    errors = md.get("ftrace_setup_errors")
    return MetaMetrics(
        window_s=(window[1] - window[0]) / 1e9,
        android_sdk=md.get("android_sdk_version"),
        build_fingerprint=md.get("android_build_fingerprint"),
        data_sources={k: bool(rows(tp, q)) for k, q in _PRESENCE.items()} | {"sidecar": has_sidecar},
        ftrace_setup_errors=_relevant_errors(errors),
    )


# atrace categories pull in many vendor display events (mdss/, sde/, ...) that
# fail harmlessly; only failures of events this tool depends on are reported.
_RELEVANT = ("sched/", "power/", "thermal/", "task/", "gpu", "kgsl")


def _relevant_errors(errors: object) -> list[str]:
    if not isinstance(errors, str):
        return []
    return [e.strip() for e in errors.splitlines() if any(k in e for k in _RELEVANT)]
