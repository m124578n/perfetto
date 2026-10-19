import re

import pytest

from gameperf_agent.record import DEFAULT_CONFIG, RecordError, render_config


def test_render_config_sets_duration():
    out = render_config(DEFAULT_CONFIG.read_text(encoding="utf-8"), 45)
    assert re.findall(r"^duration_ms: (\d+)", out, flags=re.M) == ["45000"]


def test_render_config_requires_duration_line():
    with pytest.raises(RecordError):
        render_config("buffers { size_kb: 1 }\n", 10)


def test_config_avoids_fields_unknown_to_perfetto_v46():
    # A Galaxy S24 (Android 15) runs Perfetto v46, whose text parser rejects these.
    text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    for field in ("thermal_period_ms", "gpufreq_period_ms"):
        assert not re.search(rf"^\s*{field}", text, flags=re.M)
