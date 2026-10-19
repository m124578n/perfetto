"""LLM interpretation: metrics JSON in, structured Verdict out.

Claude only ever sees the deterministic metrics (never the raw trace) and must
cite every number by its metric_path, so grounding.check() can verify it.
The rule baseline's verdict is deliberately NOT shown, to keep the two
interpreters independent for the eval.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from gameperf_agent.schema import TraceMetrics
from gameperf_agent.verdict import Verdict

DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
You triage mobile-game performance from metrics that were extracted deterministically from an
Android Perfetto trace (plus an optional sysfs "sidecar" sampler). You never see the raw trace.

Pick exactly one primary bottleneck category:
- cpu_bound_main_thread: the game's logic or render thread is busy nearly all the time, so frames
  are late because CPU work per frame exceeds the frame budget.
- gpu_bound: the CPU threads have slack while the GPU is saturated (high busy %, at max clock).
- scheduling_placement: the key thread is ready to run but waits for a CPU (high runnable ratio) or
  runs on little cores, i.e. the scheduler/placement, not the amount of work, is the problem.
- thermal_throttling: performance degrades over the session together with thermal evidence
  (rising temperatures, Android thermal status > 0, frequency limits that tighten over time).
- io_or_other: blocking (D state / I/O), SurfaceFlinger/display-side jank, or another identifiable
  cause outside the categories above.
- no_clear_bottleneck: frame pacing is healthy; nothing needs fixing.
- insufficient_data: the metrics cannot support a judgement (missing frames or scheduling data).

How to read the metrics:
- Every block has `available`; when false, the data is absent - never treat it as zero, never guess it.
- frames.frame_source "queue_buffer" means frames came from atrace queueBuffer calls (common for
  Unity/GLES games); there is no SurfaceFlinger jank classification then, use slow_frame_rate,
  fps_1pct_low and frame_interval_ms instead. Display refresh may be 60/90/120 Hz: judge pacing by the
  median interval vs. the slow tail, not against a fixed 60 fps.
- frames.segments split the session into early/mid/late thirds; a falling avg_fps suggests degradation.
- threads.threads[].cpu_util is running time / window; runnable_ratio is time waiting for a CPU /
  (running + waiting). Short bursty worker threads naturally show high runnable_ratio; weigh the
  key game_logic / render threads most.
- CPU frequency caps (thermal.cpu_caps) and GPU caps (gpu.cap_min_mhz, gpu.thermal_pwrlevel_max) can
  come from thermal mitigation OR from a vendor game power policy applied as soon as a game starts.
  A cap that is constant from the start of the trace, at normal temperature and thermal status 0,
  is not by itself evidence of thermal throttling.
- sidecar_alignment.match_ratio well above shifted_baseline_ratio means sidecar data is time-aligned.

Rules for your answer:
- Every evidence item must cite one metric by its exact dot path into the JSON (list items by
  index, e.g. "threads.threads.0.cpu_util", "frames.segments.2.avg_fps") and copy its value exactly
  as it appears. Do not compute new numbers for the value field; describe comparisons in `claim`.
- Give 2-6 evidence items, strongest first. Include evidence against close alternatives where useful.
- Set alternative_category to the runner-up when the call is close, else null.
- Confidence: high only when several independent signals agree; low when key data is missing.
- next_steps: 2-4 concrete things to measure or inspect next (data sources, Perfetto queries, tools).
"""


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolves $ref/$defs and makes every object strict (additionalProperties: false)."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: walk(v) for k, v in node.items() if k not in ("$defs", "default", "title")}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def verdict_schema() -> dict[str, Any]:
    return _inline_refs(Verdict.model_json_schema())


def user_message(metrics: TraceMetrics) -> str:
    return (
        "Metrics JSON for one trace follows. Classify the primary bottleneck.\n\n"
        + metrics.model_dump_json(indent=1)
    )


@dataclass
class LlmResult:
    verdict: Verdict | None
    model_requested: str
    model_served: str | None
    stop_reason: str | None
    raw_text: str
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def interpret(
    metrics: TraceMetrics,
    model: str = DEFAULT_MODEL,
    client: Any = None,
    effort: str | None = None,
) -> LlmResult:
    """Calls Claude once. Never raises on model-side problems; returns them in LlmResult.error."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login`

    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": verdict_schema()}}
    if effort:
        output_config["effort"] = effort

    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",  # re-run server-side on another model if a safety classifier declines
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message(metrics)}],
        output_config=output_config,
    )

    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "")
    usage = getattr(response, "usage", None)
    result = LlmResult(
        verdict=None,
        model_requested=model,
        model_served=getattr(response, "model", None),
        stop_reason=response.stop_reason,
        raw_text=text,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
    if response.stop_reason == "refusal":
        result.error = "refused by every model in the fallback chain"
        return result
    if response.stop_reason == "max_tokens":
        result.error = "output truncated at max_tokens"
        return result
    try:
        result.verdict = Verdict.model_validate(json.loads(text))
    except (ValueError, TypeError) as e:
        result.error = f"invalid verdict JSON: {e}"
    return result

