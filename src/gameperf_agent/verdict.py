"""The shared verdict format produced by both the rule baseline and the LLM."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "cpu_bound_main_thread",
    "gpu_bound",
    "scheduling_placement",
    "thermal_throttling",
    "io_or_other",
    "no_clear_bottleneck",
    "insufficient_data",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]

CATEGORY_ZH = {
    "cpu_bound_main_thread": "CPU-bound（主執行緒）",
    "gpu_bound": "GPU-bound",
    "scheduling_placement": "排程／大小核放置不當",
    "thermal_throttling": "thermal 降頻",
    "io_or_other": "I/O 或其他",
    "no_clear_bottleneck": "無明顯瓶頸",
    "insufficient_data": "資料不足",
}


class Evidence(BaseModel):
    claim: str = Field(description="One sentence supporting the verdict.")
    metric_path: str = Field(
        description="Dot path into the metrics JSON, list items by index, e.g. "
        "'frames.fps_1pct_low' or 'threads.threads.0.cpu_util'."
    )
    value: float | int | str | bool | None = Field(description="The value at metric_path, copied exactly.")


class Verdict(BaseModel):
    category: Category
    confidence: Literal["low", "medium", "high"]
    evidence: list[Evidence]
    alternative_category: Category | None = None
    next_steps: list[str] = Field(default_factory=list)
