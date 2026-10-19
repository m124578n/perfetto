"""Checks that every number a verdict cites actually exists in the metrics JSON.

This is the mechanical half of "don't trust the model": a citation passes only
if its metric_path resolves and the cited value matches (1% relative or 0.01
absolute tolerance; percent-vs-ratio confusion counts as a mismatch).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from gameperf_agent.schema import TraceMetrics
from gameperf_agent.verdict import Verdict

Status = Literal["ok", "missing_path", "value_mismatch"]


class CitationCheck(BaseModel):
    metric_path: str
    cited: Any
    actual: Any = None
    status: Status


class GroundingReport(BaseModel):
    checks: list[CitationCheck]

    @property
    def passed(self) -> int:
        return sum(c.status == "ok" for c in self.checks)

    @property
    def ratio(self) -> float | None:
        return self.passed / len(self.checks) if self.checks else None


_MISSING = object()


def resolve(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def values_match(cited: Any, actual: Any) -> bool:
    if isinstance(actual, bool) or isinstance(cited, bool):
        return cited == actual
    if isinstance(actual, (int, float)) and isinstance(cited, (int, float)):
        return abs(cited - actual) <= max(0.01, 0.01 * abs(actual))
    if isinstance(actual, (int, float)) and isinstance(cited, str):
        try:
            return values_match(float(cited), actual)
        except ValueError:
            return False
    return cited == actual


def check(verdict: Verdict, metrics: TraceMetrics | dict) -> GroundingReport:
    data = metrics.model_dump(mode="json") if isinstance(metrics, TraceMetrics) else metrics
    out = []
    for e in verdict.evidence:
        actual = resolve(data, e.metric_path)
        if actual is _MISSING:
            out.append(CitationCheck(metric_path=e.metric_path, cited=e.value, status="missing_path"))
        else:
            status = "ok" if values_match(e.value, actual) else "value_mismatch"
            out.append(CitationCheck(metric_path=e.metric_path, cited=e.value, actual=actual, status=status))
    return GroundingReport(checks=out)
