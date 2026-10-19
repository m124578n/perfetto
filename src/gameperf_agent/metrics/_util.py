"""Small helpers shared by the metric extractors."""

from __future__ import annotations

import math
from typing import Any

from perfetto.trace_processor import TraceProcessor

from gameperf_agent.schema import IntervalStats


def rows(tp: TraceProcessor, sql: str) -> list[Any]:
    return list(tp.query(sql))


def sql_str(value: str) -> str:
    """Quotes a value as a SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (same as numpy's default)."""
    if not sorted_values:
        raise ValueError("empty")
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def interval_stats(values: list[float]) -> IntervalStats:
    if not values:
        return IntervalStats(count=0)
    s = sorted(values)
    return IntervalStats(
        count=len(s),
        mean=sum(s) / len(s),
        p50=percentile(s, 0.50),
        p90=percentile(s, 0.90),
        p99=percentile(s, 0.99),
        max=s[-1],
    )
