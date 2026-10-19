"""Step-function helpers for counters (a value holds until the next sample)."""

from __future__ import annotations

from dataclasses import dataclass

SEGMENT_LABELS = ("early", "mid", "late")


@dataclass(frozen=True)
class Sample:
    ts: int
    value: float


def clip_steps(samples: list[Sample], start: int, end: int) -> list[tuple[int, int, float]]:
    """Returns (seg_start, seg_end, value) pieces covering [start, end).

    The last sample before `start` carries into the window. Time before the
    first sample in the window is uncovered (not assumed).
    """
    s = sorted(samples, key=lambda x: x.ts)
    pieces = []
    for i, cur in enumerate(s):
        nxt = s[i + 1].ts if i + 1 < len(s) else end
        lo, hi = max(cur.ts, start), min(nxt, end)
        if hi > lo:
            pieces.append((lo, hi, cur.value))
    return pieces


def time_weighted_mean(pieces: list[tuple[int, int, float]]) -> float | None:
    total = sum(hi - lo for lo, hi, _ in pieces)
    if not total:
        return None
    return sum((hi - lo) * v for lo, hi, v in pieces) / total


def time_share(pieces: list[tuple[int, int, float]], predicate) -> float | None:
    total = sum(hi - lo for lo, hi, _ in pieces)
    if not total:
        return None
    return sum(hi - lo for lo, hi, v in pieces if predicate(v)) / total


def thirds(start: int, end: int) -> list[tuple[str, int, int]]:
    width = (end - start) / 3
    return [(label, int(start + i * width), int(start + (i + 1) * width)) for i, label in enumerate(SEGMENT_LABELS)]
