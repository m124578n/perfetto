import pytest
from synth import MS, SynthTrace

from gameperf_agent.metrics.cpu_topology import extract_topology
from gameperf_agent.metrics.gpu import extract_gpu
from gameperf_agent.metrics.thermal import extract_thermal
from gameperf_agent.sidecar import alignment_check, parse_sidecar

WINDOW = (1_000 * MS, 1_300 * MS)
CLUSTERS = [(2, 0, [500000, 2000000]), (2, 0, [500000, 3000000])]


def _csv(rows: list[tuple[float, str, object]]) -> str:
    return "uptime_s,key,value\n" + "".join(f"{u},{k},{v}\n" for u, k, v in rows)


def _sidecar_text() -> str:
    rows = [(1.0, "gpu_hw_max_mhz", 1000), (1.0, "hw_max_khz:policy0", 2000000), (1.0, "hw_max_khz:policy2", 3000000)]
    for i in range(30):  # every 10 ms from 1.000 s to 1.290 s
        u = round(1.0 + i * 0.01, 2)
        late = i >= 20
        rows += [
            (u, "gpu_clock_mhz", 1000 if i < 20 else 500),
            (u, "gpu_busy_pct", 95 if i < 20 else 60),
            (u, "gpu_cap_mhz", 1000 if i < 20 else 500),
            (u, "gpu_thermal_pwrlevel", 0 if i < 20 else 4),
            (u, "temp_mc:gpuss-0", 40000 + i * 500),
            (u, "temp_mc:gpuss-1", 39000 + i * 500),
            (u, "temp_mc:battery", 30000),
            (u, "max_khz:policy0", 2000000),
            (u, "max_khz:policy2", 1500000 if late else 3000000),
            (u, "cur_khz:policy2", 1500000 if late else 3000000),
        ]
        if i % 5 == 0:
            rows.append((u, "thermal_status", 3 if late else 0))
    return _csv(rows)


def test_parse_sidecar():
    sc = parse_sidecar(_sidecar_text())
    assert sc.static["gpu_hw_max_mhz"] == 1000
    assert sc.static["hw_max_khz:policy2"] == 3000000
    assert sc.series["gpu_clock_mhz"][0].ts == 1_000 * MS
    assert len(sc.series["gpu_clock_mhz"]) == 30
    assert sc.series["thermal_status"][-1].value == 3


def test_parse_skips_malformed_rows():
    sc = parse_sidecar("uptime_s,key,value\n1.0,gpu_clock_mhz,500\n1.01,gpu_clock_mhz,\ngarbage\n1.02,thermal_status,x\n")
    assert [s.value for s in sc.series["gpu_clock_mhz"]] == [500]
    assert "thermal_status" not in sc.series


def test_gpu_from_sidecar(load):
    tp = load(SynthTrace().add_processes({1: "init"}))
    g = extract_gpu(tp, parse_sidecar(_sidecar_text()), WINDOW)
    assert g.available and g.source == "sidecar"
    assert g.hw_max_mhz == 1000
    # 200 ms at 1000 MHz, then 100 ms at 500 MHz (the last sample holds to window end).
    assert g.avg_mhz == pytest.approx((200 * 1000 + 100 * 500) / 300)
    assert g.time_at_hw_max_ratio == pytest.approx(200 / 300)
    assert g.avg_busy_pct == pytest.approx((200 * 95 + 100 * 60) / 300)
    assert g.segments[0].max == 1000 and g.segments[-1].max == 500
    assert g.cap_min_mhz == 500
    assert g.capped_time_ratio == pytest.approx(100 / 300)
    assert g.thermal_pwrlevel_max == 4


def test_gpu_unavailable_without_any_source(load):
    g = extract_gpu(load(SynthTrace().add_processes({1: "init"})), None, WINDOW)
    assert not g.available
    assert g.avg_mhz is None
    assert "sidecar" in g.reason


def test_thermal_from_sidecar(load):
    tp = load(SynthTrace().add_cpus(CLUSTERS))
    t = extract_thermal(tp, parse_sidecar(_sidecar_text()), extract_topology(tp), WINDOW)
    assert t.available
    zones = {z.zone: z for z in t.zones}
    # gpuss-0/1 grouped into "gpuss" (max across members); milli-C -> C.
    gpu = zones["gpuss"]
    assert gpu.start_c == pytest.approx(40.0)
    assert gpu.max_c == pytest.approx(40.0 + 29 * 0.5)
    assert gpu.delta_c == pytest.approx(29 * 0.5)
    assert zones["battery"].delta_c == 0
    assert t.thermal_status_max == 3
    assert t.thermal_status_ge_severe_ratio == pytest.approx(100 / 300)
    caps = {c.cluster: c for c in t.cpu_caps}
    assert caps["big"].min_cap_ratio == pytest.approx(0.5)
    assert caps["big"].capped_time_ratio == pytest.approx(100 / 300)
    assert caps["little"].capped_time_ratio == 0


def test_thermal_unavailable(load):
    tp = load(SynthTrace().add_cpus(CLUSTERS))
    t = extract_thermal(tp, None, extract_topology(tp), WINDOW)
    assert not t.available
    assert t.zones == [] and t.thermal_status_max is None


def test_alignment_check_matches_trace_cpufreq(load):
    trace = SynthTrace().add_cpus(CLUSTERS)
    trace.add_cpu_freq(2, [(1_000 * MS, 3_000_000), (1_200 * MS, 1_500_000), (1_300 * MS, 1_500_000)])
    tp = load(trace)
    good = alignment_check(tp, parse_sidecar(_sidecar_text()))
    assert good.compared_samples > 0
    assert good.match_ratio == pytest.approx(1.0)
    # 300 ms of data: shifting by 1-2 s leaves no overlap, so no baseline.
    assert good.shifted_baseline_ratio is None

    # Same data with the sidecar clock shifted by 50 ms -> visible mismatches.
    rows = [line.split(",") for line in _sidecar_text().strip().split("\n")[1:]]
    shifted = _csv([(round(float(u) + 0.05, 2), k, v) for u, k, v in rows])
    bad = alignment_check(tp, parse_sidecar(shifted))
    assert bad.match_ratio < 0.9
