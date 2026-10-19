import pytest
from synth import MS, SynthTrace

from gameperf_agent.metrics.cpu_topology import extract_topology
from gameperf_agent.metrics.cpufreq import extract_cpufreq

TWO_CLUSTERS = [(2, 0, [500000, 1000000, 2000000]), (2, 0, [500000, 1500000, 3000000])]
WINDOW = (1_000 * MS, 1_300 * MS)


def _trace() -> SynthTrace:
    t = SynthTrace().add_cpus(TWO_CLUSTERS)
    # little (cpu0-1): 100 ms @1.0 GHz, then 200 ms @2.0 GHz.
    for cpu in (0, 1):
        t.add_cpu_freq(cpu, [(1_000 * MS, 1_000_000), (1_100 * MS, 2_000_000)])
    # big (cpu2-3): 3.0 GHz for the first third, then capped to 1.5 GHz.
    for cpu in (2, 3):
        t.add_cpu_freq(cpu, [(1_000 * MS, 3_000_000), (1_100 * MS, 1_500_000)])
    return t


def test_time_weighted_average_and_near_max_share(load):
    tp = load(_trace())
    m = extract_cpufreq(tp, extract_topology(tp), WINDOW)
    assert m.available
    little, big = m.clusters
    assert little.cluster == "little" and little.cpus == [0, 1]
    assert little.avg_mhz == pytest.approx((100 * 1000 + 200 * 2000) / 300)
    assert little.min_mhz == 1000 and little.max_mhz == 2000
    assert little.hw_max_mhz == 2000
    assert little.time_at_hw_max_ratio == pytest.approx(200 / 300)
    assert big.avg_mhz == pytest.approx((100 * 3000 + 200 * 1500) / 300)


def test_segments_reveal_frequency_cap(load):
    tp = load(_trace())
    big = extract_cpufreq(tp, extract_topology(tp), WINDOW).clusters[1]
    early, mid, late = big.segments
    assert early.max == 3000
    assert late.max == 1500
    assert big.late_vs_early_max_ratio == pytest.approx(0.5)


def test_value_before_window_carries_into_it(load):
    tp = load(_trace())
    # Window starts mid-way through the 1.0 GHz period; the earlier sample still applies.
    little = extract_cpufreq(tp, extract_topology(tp), (1_050 * MS, 1_150 * MS)).clusters[0]
    assert little.avg_mhz == pytest.approx(1500)


def test_no_cpufreq(load):
    tp = load(SynthTrace().add_cpus(TWO_CLUSTERS))
    m = extract_cpufreq(tp, extract_topology(tp), WINDOW)
    assert not m.available
    assert m.clusters == []
