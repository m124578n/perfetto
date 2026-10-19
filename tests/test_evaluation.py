import pytest

from gameperf_agent.evaluation import (
    TraceResult,
    accuracy,
    confusion,
    confusion_markdown,
    load_labels,
    majority,
    render_summary,
    summarize,
)


def test_majority_and_stability():
    assert majority(["gpu_bound", "gpu_bound", "thermal_throttling"]) == ("gpu_bound", pytest.approx(2 / 3))
    assert majority([None, None]) == (None, 0.0)
    # A failed run counts against stability.
    assert majority(["gpu_bound", None]) == ("gpu_bound", 0.5)


def test_accuracy_and_confusion():
    pairs = [("gpu_bound", "gpu_bound"), ("gpu_bound", "thermal_throttling"), ("thermal_throttling", None)]
    assert accuracy(pairs) == pytest.approx(1 / 3)
    m = confusion(pairs)
    assert m["gpu_bound"]["thermal_throttling"] == 1
    assert m["thermal_throttling"]["failed"] == 1
    md = confusion_markdown(m)
    assert "`gpu_bound`" in md and "`failed`" in md and "`io_or_other`" not in md


def test_load_labels_splits_pending_and_rejects_typos(tmp_path):
    p = tmp_path / "labels.yaml"
    p.write_text(
        "traces:\n"
        "  - {id: a, trace: traces/a.pftrace, label: gpu_bound, reason: r}\n"
        "  - {id: b, trace: traces/b.pftrace, label: TODO}\n",
        encoding="utf-8",
    )
    labeled, pending = load_labels(p)
    assert [t.id for t in labeled] == ["a"] and [t.id for t in pending] == ["b"]

    p.write_text("traces:\n  - {id: c, trace: x, label: gpu-bound}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown label"):
        load_labels(p)


def test_summary_renders():
    results = [
        TraceResult(id="a", human="gpu_bound", human_reason="r", rule="gpu_bound",
                    llm_runs=["gpu_bound"] * 3, llm_majority="gpu_bound", llm_stability=1.0,
                    grounding_passed=6, grounding_total=6),
        TraceResult(id="b", human="thermal_throttling", human_reason="hot", rule="cpu_bound_main_thread",
                    llm_runs=["gpu_bound", "gpu_bound", None], llm_majority="gpu_bound", llm_stability=2 / 3,
                    grounding_passed=3, grounding_total=4, llm_top_evidence=["x"]),
    ]
    s = summarize(results, [], "claude-sonnet-5-5", 3)
    assert s["llm_accuracy"] == 0.5 and s["rule_accuracy"] == 0.5
    assert s["grounding_pass_rate"] == pytest.approx(0.9)
    md = render_summary(s, results, [])
    assert "Disagreements" in md and "### b" in md
    assert "Treat these as anecdotes" in md
