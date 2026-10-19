"""Eval: agreement of LLM and rule verdicts with human labels."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gameperf_agent.verdict import CATEGORIES, CATEGORY_ZH

PENDING = "TODO"


@dataclass
class LabeledTrace:
    id: str
    trace: str
    label: str
    reason: str = ""
    labeler_confidence: str = ""
    scenario: str = ""
    sidecar: str | None = None
    package: str | None = None


def load_labels(path: str | Path) -> tuple[list[LabeledTrace], list[LabeledTrace]]:
    """Returns (labeled, pending). Raises on unknown categories so typos cannot slip into results."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    labeled, pending = [], []
    for item in data.get("traces") or []:
        t = LabeledTrace(**{k: item.get(k) for k in LabeledTrace.__dataclass_fields__ if k in item})
        if t.label == PENDING or not t.label:
            pending.append(t)
        elif t.label not in CATEGORIES:
            raise ValueError(f"{t.id}: unknown label {t.label!r}; expected one of {CATEGORIES}")
        else:
            labeled.append(t)
    return labeled, pending


def majority(categories: list[str | None]) -> tuple[str | None, float]:
    """Most common non-None answer and the share of runs that gave it (ties: first seen)."""
    valid = [c for c in categories if c]
    if not valid:
        return None, 0.0
    top, n = Counter(valid).most_common(1)[0]
    return top, n / len(categories)


def accuracy(pairs: list[tuple[str, str | None]]) -> float | None:
    return sum(a == b for a, b in pairs) / len(pairs) if pairs else None


def confusion(pairs: list[tuple[str, str | None]]) -> dict[str, dict[str, int]]:
    """rows = human label, columns = predicted (None -> 'failed')."""
    cols = list(CATEGORIES) + ["failed"]
    m = {r: {c: 0 for c in cols} for r in CATEGORIES}
    for human, pred in pairs:
        m[human][pred or "failed"] += 1
    return m


def confusion_markdown(m: dict[str, dict[str, int]]) -> str:
    rows = [r for r in m if any(m[r].values())]
    cols = [c for c in next(iter(m.values())) if any(m[r][c] for r in m)]
    if not rows:
        return "_no data_"
    head = "| human \\ predicted | " + " | ".join(f"`{c}`" for c in cols) + " |"
    sep = "|---|" + "---|" * len(cols)
    body = [f"| `{r}` | " + " | ".join(str(m[r][c]) if m[r][c] else "·" for c in cols) + " |" for r in rows]
    return "\n".join([head, sep, *body])


@dataclass
class TraceResult:
    id: str
    human: str
    human_reason: str
    rule: str
    llm_runs: list[str | None] = field(default_factory=list)
    llm_majority: str | None = None
    llm_stability: float = 0.0
    grounding_passed: int = 0
    grounding_total: int = 0
    llm_errors: list[str] = field(default_factory=list)
    llm_top_evidence: list[str] = field(default_factory=list)


def summarize(results: list[TraceResult], pending: list[LabeledTrace], model: str | None, runs: int) -> dict[str, Any]:
    llm_pairs = [(r.human, r.llm_majority) for r in results if r.llm_runs]
    rule_pairs = [(r.human, r.rule) for r in results]
    g_total = sum(r.grounding_total for r in results)
    return {
        "n_labeled": len(results),
        "n_pending": len(pending),
        "model": model,
        "runs_per_trace": runs,
        "llm_accuracy": accuracy(llm_pairs),
        "rule_accuracy": accuracy(rule_pairs),
        "llm_rule_agreement": accuracy([(r.rule, r.llm_majority) for r in results if r.llm_runs]),
        "llm_mean_stability": (sum(r.llm_stability for r in results if r.llm_runs) / len(llm_pairs)) if llm_pairs else None,
        "grounding_pass_rate": (sum(r.grounding_passed for r in results) / g_total) if g_total else None,
        "label_distribution": dict(Counter(r.human for r in results)),
        "llm_confusion": confusion(llm_pairs),
        "rule_confusion": confusion(rule_pairs),
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def render_summary(s: dict[str, Any], results: list[TraceResult], pending: list[LabeledTrace]) -> str:
    out = ["# Eval results", ""]
    n = s["n_labeled"]
    out += [
        f"- Labeled traces: **{n}** (pending labels: {s['n_pending']})",
        f"- Model: `{s['model'] or 'not run'}`, {s['runs_per_trace']} run(s) per trace (majority vote)",
        f"- Label distribution: {', '.join(f'`{k}` {v}' for k, v in s['label_distribution'].items()) or '—'}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| LLM vs human agreement | {_pct(s['llm_accuracy'])} |",
        f"| Rule baseline vs human agreement | {_pct(s['rule_accuracy'])} |",
        f"| LLM vs rule agreement | {_pct(s['llm_rule_agreement'])} |",
        f"| LLM run-to-run stability (share of runs matching the majority) | {_pct(s['llm_mean_stability'])} |",
        f"| Grounding: cited numbers that match the metrics | {_pct(s['grounding_pass_rate'])} |",
        "",
    ]
    if 0 < n < 30:
        out += [f"> With n = {n}, one trace moves agreement by {1 / n:.0%}. Treat these as anecdotes, "
                "not accuracy estimates.", ""]
    out += ["## Confusion matrix: LLM (majority)", "", confusion_markdown(s["llm_confusion"]), "",
            "## Confusion matrix: rule baseline", "", confusion_markdown(s["rule_confusion"]), "",
            "## Per-trace", "",
            "| trace | human | rule | LLM (runs) | grounding | human reason |",
            "|---|---|---|---|---|---|"]
    for r in results:
        mark = lambda pred: "✅" if pred == r.human else "❌"  # noqa: E731
        runs = ", ".join(c or "fail" for c in r.llm_runs) or "not run"
        llm = f"{mark(r.llm_majority)} `{r.llm_majority}` ({runs})" if r.llm_runs else "not run"
        g = f"{r.grounding_passed}/{r.grounding_total}" if r.grounding_total else "—"
        out.append(f"| {r.id} | `{r.human}` | {mark(r.rule)} `{r.rule}` | {llm} | {g} | {r.human_reason} |")
    disagreements = [r for r in results if r.llm_runs and r.llm_majority != r.human]
    if disagreements:
        out += ["", "## Disagreements (LLM vs human)", ""]
        for r in disagreements:
            out += [f"### {r.id}: human `{r.human}` ({CATEGORY_ZH[r.human]}), LLM `{r.llm_majority}`", "",
                    f"Human reason: {r.human_reason}", "", "LLM's top evidence:", ""]
            out += [f"- {e}" for e in r.llm_top_evidence] or ["- (none)"]
            if r.llm_errors:
                out += [f"- errors: {'; '.join(r.llm_errors)}"]
            out.append("")
    if pending:
        out += ["", "## Pending labels", ""] + [f"- {p.id}: `{p.trace}`" for p in pending]
    return "\n".join(out) + "\n"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
