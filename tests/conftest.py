import sys
from pathlib import Path

import pytest
from perfetto.trace_processor import TraceProcessor

sys.path.insert(0, str(Path(__file__).parent))

from synth import SynthTrace  # noqa: E402


@pytest.fixture
def load():
    """Loads a SynthTrace into a TraceProcessor; closes all of them at teardown."""
    opened: list[TraceProcessor] = []

    def _load(trace: SynthTrace) -> TraceProcessor:
        tp = TraceProcessor(trace=trace.to_file())
        opened.append(tp)
        return tp

    yield _load
    for tp in opened:
        tp.close()
