"""Runs every deterministic extractor over one trace (no LLM involved)."""

from __future__ import annotations

from pathlib import Path
from typing import IO

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.metrics.cpu_topology import extract_topology
from gameperf_agent.metrics.cpufreq import extract_cpufreq
from gameperf_agent.metrics.frames import extract_frames
from gameperf_agent.metrics.gpu import extract_gpu
from gameperf_agent.metrics.meta import analysis_window, extract_meta
from gameperf_agent.metrics.process import select_target_process
from gameperf_agent.metrics.thermal import extract_thermal
from gameperf_agent.metrics.threads import extract_threads
from gameperf_agent.schema import TraceMetrics
from gameperf_agent.sidecar import Sidecar, alignment_check, load_sidecar


def default_sidecar_path(trace_path: str | Path) -> Path | None:
    """<name>.pftrace -> <name>.sidecar.csv next to it, if present."""
    p = Path(trace_path)
    candidate = p.with_name(p.name.split(".")[0] + ".sidecar.csv")
    return candidate if candidate.exists() else None


def extract_all(
    tp: TraceProcessor, trace_name: str, sidecar: Sidecar | None = None, package: str | None = None
) -> TraceMetrics:
    window = analysis_window(tp)
    target = select_target_process(tp, package)
    topology = extract_topology(tp)
    return TraceMetrics(
        trace_file=trace_name,
        meta=extract_meta(tp, window, has_sidecar=sidecar is not None),
        target=target,
        topology=topology,
        frames=extract_frames(tp, target.upid),
        threads=extract_threads(tp, target.upid, topology, window),
        cpufreq=extract_cpufreq(tp, topology, window),
        gpu=extract_gpu(tp, sidecar, window),
        thermal=extract_thermal(tp, sidecar, topology, window),
        sidecar_alignment=alignment_check(tp, sidecar),
    )


def analyze_trace(
    trace: str | Path | IO[bytes],
    sidecar_path: str | Path | None = None,
    package: str | None = None,
) -> TraceMetrics:
    if sidecar_path is None and isinstance(trace, (str, Path)):
        sidecar_path = default_sidecar_path(trace)
    name = Path(trace).name if isinstance(trace, (str, Path)) else "<memory>"
    tp = TraceProcessor(trace=str(trace) if isinstance(trace, Path) else trace)
    try:
        return extract_all(tp, name, load_sidecar(sidecar_path), package)
    finally:
        tp.close()
