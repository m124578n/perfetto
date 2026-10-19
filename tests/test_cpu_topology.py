from synth import MS, SynthTrace

from gameperf_agent.metrics.cpu_topology import extract_topology

# Galaxy S24 (Snapdragon 8 Gen 3) shape: cpu2-4 are faster than cpu5-6.
S24 = [(2, 0, [300000, 2265600]), (3, 0, [400000, 3148800]), (2, 0, [400000, 2956800]), (1, 0, [500000, 3398400])]


def test_four_clusters_ranked_by_max_available_freq(load):
    topo = extract_topology(load(SynthTrace().add_cpus(S24)))
    assert topo.available
    assert topo.ordering_basis == "max_available_freq"
    assert [(c.name, c.cpus, c.max_freq_khz) for c in topo.clusters] == [
        ("little", [0, 1], 2265600),
        ("mid", [5, 6], 2956800),
        ("big", [2, 3, 4], 3148800),
        ("prime", [7], 3398400),
    ]
    assert topo.cluster_name_by_cpu() == {0: "little", 1: "little", 2: "big", 3: "big", 4: "big",
                                          5: "mid", 6: "mid", 7: "prime"}


def test_capacity_wins_over_frequency(load):
    # Pixel 6 style with capacities; make the frequency order disagree on purpose.
    trace = SynthTrace().add_cpus([(4, 160, [300000, 1803000]), (2, 498, [400000, 2900000]), (2, 1024, [500000, 2802000])])
    topo = extract_topology(load(trace))
    assert topo.ordering_basis == "capacity"
    assert [c.name for c in topo.clusters] == ["little", "mid", "big"]
    assert [c.capacity for c in topo.clusters] == [160, 498, 1024]


def test_two_clusters_are_little_and_big(load):
    topo = extract_topology(load(SynthTrace().add_cpus([(4, 0, [1000, 1800000]), (4, 0, [1000, 2400000])])))
    assert [c.name for c in topo.clusters] == ["little", "big"]


def test_falls_back_to_observed_frequency_without_cpu_info(load):
    trace = SynthTrace()
    trace.add_cpu_freq(0, [(1_000 * MS, 1_000_000), (1_001 * MS, 1_800_000)])
    trace.add_cpu_freq(1, [(1_000 * MS, 1_000_000), (1_001 * MS, 1_800_000)])
    trace.add_cpu_freq(2, [(1_000 * MS, 2_000_000), (1_001 * MS, 3_000_000)])
    topo = extract_topology(load(trace))
    assert topo.available
    assert topo.ordering_basis == "observed_max_freq"
    assert [(c.name, c.cpus) for c in topo.clusters] == [("little", [0, 1]), ("big", [2])]


def test_no_topology_data(load):
    topo = extract_topology(load(SynthTrace().add_processes({1: "init"})))
    assert not topo.available
    assert topo.clusters == []
    assert topo.reason
