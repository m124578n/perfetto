"""LLM plumbing tests with a fake client (no network, no API key)."""

import json
from types import SimpleNamespace

from factories import healthy

from gameperf_agent.llm import DEFAULT_MODEL, FALLBACK_BETA, interpret, user_message, verdict_schema
from gameperf_agent.verdict import CATEGORIES


class FakeClient:
    def __init__(self, text: str, stop_reason: str = "end_turn", model: str = DEFAULT_MODEL):
        self.calls = []
        response = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            stop_reason=stop_reason, model=model, usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: self.calls.append(kw) or response))


GOOD = {
    "category": "gpu_bound",
    "confidence": "medium",
    "evidence": [{"claim": "GPU busy", "metric_path": "gpu.avg_busy_pct", "value": 40}],
    "alternative_category": None,
    "next_steps": ["Capture GPU counters."],
}


def test_schema_is_strict_and_self_contained():
    schema = verdict_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["category"]["enum"]) == set(CATEGORIES)
    evidence_item = schema["properties"]["evidence"]["items"]
    assert evidence_item["additionalProperties"] is False
    assert set(evidence_item["required"]) == {"claim", "metric_path", "value"}


def test_request_shape():
    client = FakeClient(json.dumps(GOOD))
    interpret(healthy(), client=client, effort="medium")
    (kw,) = client.calls
    assert kw["model"] == DEFAULT_MODEL
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "medium"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Only metrics go to the model - no raw trace, no rule verdict.
    assert kw["messages"][0]["content"] == user_message(healthy())
    assert "rule" not in kw["messages"][0]["content"].lower()


def test_parses_verdict():
    r = interpret(healthy(), client=FakeClient(json.dumps(GOOD)))
    assert r.error is None
    assert r.verdict.category == "gpu_bound"
    assert r.input_tokens == 1000


def test_refusal_and_bad_json_are_reported_not_raised():
    assert interpret(healthy(), client=FakeClient("", stop_reason="refusal")).error.startswith("refused")
    bad = interpret(healthy(), client=FakeClient('{"category": "made_up"}'))
    assert bad.verdict is None and "invalid" in bad.error


def test_fallback_model_is_recorded():
    r = interpret(healthy(), client=FakeClient(json.dumps(GOOD), model="claude-opus-4-8"))
    assert r.model_requested == DEFAULT_MODEL and r.model_served == "claude-opus-4-8"
