from factories import healthy

from gameperf_agent.grounding import check, resolve, values_match
from gameperf_agent.verdict import Evidence, Verdict


def _v(*evidence):
    return Verdict(category="gpu_bound", confidence="low", evidence=[Evidence(claim="c", metric_path=p, value=v)
                                                                       for p, v in evidence])


def test_resolve_dicts_and_list_indices():
    data = {"a": {"b": [{"c": 3}]}}
    assert resolve(data, "a.b.0.c") == 3


def test_values_match_tolerance():
    assert values_match(60.0, 60.4)
    assert not values_match(60.0, 62.0)
    assert values_match(0.004, 0.0)  # absolute tolerance near zero
    assert not values_match(57, 0.57)  # percent vs ratio is a real error
    assert values_match("16.7", 16.7)
    assert values_match(False, False)


def test_check_flags_missing_paths_and_wrong_values():
    m = healthy()
    report = check(_v(("frames.avg_fps", 60), ("frames.avg_fps", 75), ("frames.nonexistent", 1),
                      ("threads.threads.5.cpu_util", 0.5)), m)
    assert [c.status for c in report.checks] == ["ok", "value_mismatch", "missing_path", "missing_path"]
    assert report.ratio == 0.25
