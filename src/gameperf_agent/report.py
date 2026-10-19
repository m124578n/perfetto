"""Renders the Markdown triage report."""

from __future__ import annotations

from gameperf_agent.grounding import GroundingReport
from gameperf_agent.llm import LlmResult
from gameperf_agent.schema import TraceMetrics
from gameperf_agent.verdict import CATEGORY_ZH, Verdict

NA = "此 trace 無此資料"
STATUS_MARK = {"ok": "✅", "missing_path": "❌ 路徑不存在", "value_mismatch": "❌ 數值不符"}


def _f(x, digits: int = 1, suffix: str = "") -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{digits}f}{suffix}"
    return f"{x}{suffix}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _verdict_row(who: str, v: Verdict | None, g: GroundingReport | None, note: str = "") -> str:
    if v is None:
        return f"| {who} | {note or '未執行'} | — | — |"
    grounded = f"{g.passed}/{len(g.checks)}" if g else "—"
    return f"| {who} | **{CATEGORY_ZH[v.category]}** (`{v.category}`) | {v.confidence} | {grounded} |"


def _evidence(v: Verdict, g: GroundingReport) -> list[str]:
    lines = []
    for e, c in zip(v.evidence, g.checks):
        mark = STATUS_MARK[c.status]
        actual = f"（實際值 {c.actual}）" if c.status == "value_mismatch" else ""
        lines.append(f"- {e.claim}  \n  `{e.metric_path}` = {e.value} {mark}{actual}")
    return lines


def render(
    m: TraceMetrics,
    rule: Verdict,
    rule_grounding: GroundingReport,
    llm: LlmResult | None = None,
    llm_grounding: GroundingReport | None = None,
) -> str:
    out: list[str] = []
    w = out.append
    w("# 遊戲效能瓶頸初判報告\n")
    w(f"- Trace：`{m.trace_file}`")
    w(f"- 目標 process：`{m.target.name or '—'}`（{m.target.reason}）")
    w(f"- 裝置 build：`{m.meta.build_fingerprint or '—'}`，Android SDK {m.meta.android_sdk or '—'}")
    w(f"- 分析區間：{m.meta.window_s:.1f} s\n")
    w("> 這是**初判**，不是結論：數字由程式從 trace 萃取，判讀由 LLM 與規則各自提出，"
      "每個被引用的數字都經過程式核對（grounding）。請以 Perfetto UI 驗證後再下結論。\n")

    w("## 判讀結果\n")
    w("| 判讀者 | 瓶頸類別 | 信心 | 引用數字核對 |")
    w("|---|---|---|---|")
    if llm is None:
        w(_verdict_row("LLM", None, None, "未執行（--no-llm）"))
    elif llm.verdict is None:
        w(_verdict_row(f"LLM `{llm.model_requested}`", None, None, f"失敗：{llm.error}"))
    else:
        served = llm.model_served or llm.model_requested
        who = f"LLM `{served}`" + (f"（fallback 自 `{llm.model_requested}`）" if served != llm.model_requested else "")
        w(_verdict_row(who, llm.verdict, llm_grounding))
    w(_verdict_row("規則 baseline", rule, rule_grounding))
    if llm and llm.verdict:
        agree = "一致" if llm.verdict.category == rule.category else "**不一致**，請人工檢查"
        w(f"\nLLM 與規則判讀：{agree}。")
        if llm.verdict.alternative_category:
            w(f"LLM 次要候選：{CATEGORY_ZH[llm.verdict.alternative_category]}。")
    w("")

    if llm and llm.verdict and llm_grounding:
        w("### 判斷依據（LLM）\n")
        out.extend(_evidence(llm.verdict, llm_grounding))
        w("\n### 下一步建議（LLM）\n")
        out.extend(f"- {s}" for s in llm.verdict.next_steps)
        w("")
    w("### 判斷依據（規則 baseline）\n")
    out.extend(_evidence(rule, rule_grounding))
    if rule.next_steps:
        w("")
        out.extend(f"- 下一步：{s}" for s in rule.next_steps)
    w("")

    w("## 指標摘要\n")
    f = m.frames
    w("### Frame pacing\n")
    if not f.available:
        w(f"{NA}：{f.reason}\n")
    else:
        iv = f.frame_interval_ms
        w(f"來源：`{f.frame_source}`（{f.layer_name}；present 時間：`{f.present_time_source}`）\n")
        w("| 指標 | 值 |")
        w("|---|---|")
        w(f"| frame 數 | {f.frame_count} |")
        w(f"| 平均 FPS | {_f(f.avg_fps)} |")
        w(f"| 1% low FPS | {_f(f.fps_1pct_low)} |")
        w(f"| frame interval P50 / P90 / P99 / max (ms) | {_f(iv.p50, 2)} / {_f(iv.p90, 2)} / {_f(iv.p99, 2)} / {_f(iv.max, 2)} |")
        w(f"| 慢 frame（≥1.5× 中位數）比例 | {_pct(f.slow_frame_rate)} |")
        if f.jank:
            j = f.jank
            w(f"| jank 比例（app / SF / dropped） | {_pct(j.jank_rate)}（{j.app_jank_frames} / {j.sf_jank_frames} / {j.dropped_frames}） |")
            w(f"| jank 類型 | {', '.join(f'{k}: {v}' for k, v in j.by_type.items()) or '無'} |")
        else:
            w(f"| jank 類型 | {NA}（{f.frame_source} 來源沒有 SurfaceFlinger jank 分類） |")
        w(f"| 前 / 中 / 後段 FPS | {' / '.join(_f(s.avg_fps) for s in f.segments)} |")
        w(f"| 閒置空檔（>1 s，已排除） | {f.idle_gap_count} |")
        w("")

    t = m.threads
    w("### 關鍵 thread\n")
    if not t.available:
        w(f"{NA}：{t.reason}\n")
    else:
        w("| thread | 角色 | CPU 使用率 | 等 CPU 比例 | D 狀態 (ms) | cluster 分佈 |")
        w("|---|---|---|---|---|---|")
        for th in t.threads:
            share = ", ".join(f"{k} {v:.0%}" for k, v in sorted(th.cluster_share.items(), key=lambda kv: -kv[1]))
            w(f"| {th.name} | {th.role} | {_pct(th.cpu_util)} | {_pct(th.runnable_ratio)} | "
              f"{th.uninterruptible_ms:.0f} | {share or '—'} |")
        w("")

    w("### CPU 頻率（DVFS）\n")
    if not m.cpufreq.available:
        w(f"{NA}：{m.cpufreq.reason}\n")
    else:
        w("| cluster | CPUs | 硬體上限 MHz | 平均 MHz | 平均/上限 | 前段→後段最高頻 |")
        w("|---|---|---|---|---|---|")
        for c in m.cpufreq.clusters:
            seg = f"{_f(c.segments[0].max, 0)} → {_f(c.segments[-1].max, 0)}"
            w(f"| {c.cluster} | {c.cpus} | {c.hw_max_mhz:.0f} | {_f(c.avg_mhz, 0)} | {_pct(c.avg_vs_hw_max)} | {seg} |")
        w("")

    g = m.gpu
    w("### GPU\n")
    if not g.available:
        w(f"{NA}：{g.reason}\n")
    else:
        w(f"來源：`{g.source}`；平均 {_f(g.avg_mhz, 0)} MHz（硬體上限 {_f(g.hw_max_mhz, 0)}），"
          f"平均 busy {_f(g.avg_busy_pct, 0, '%')}，動態上限最低 {_f(g.cap_min_mhz, 0)} MHz"
          f"（受限時間 {_pct(g.capped_time_ratio)}），thermal pwrlevel 最大 {_f(g.thermal_pwrlevel_max, 0)}。\n")

    th = m.thermal
    w("### Thermal\n")
    if not th.available:
        w(f"{NA}：{th.reason}\n")
    else:
        w(f"Android thermal status 最大值：{_f(th.thermal_status_max, 0)}"
          f"（≥ LIGHT 時間 {_pct(th.thermal_status_ge_light_ratio)}）\n")
        if th.zones:
            w("| zone | 來源 | 起始 °C | 最高 °C | 變化 °C |")
            w("|---|---|---|---|---|")
            for z in th.zones:
                w(f"| {z.zone} | {z.source} | {z.start_c:.1f} | {z.max_c:.1f} | {z.delta_c:+.1f} |")
            w("")
        if th.cpu_caps:
            w("CPU 頻率上限（scaling_max_freq；可能來自 thermal 或廠商遊戲策略）：")
            w("")
            w("| cluster | 最低上限 / 硬體上限 | 受限時間 |")
            w("|---|---|---|")
            for c in th.cpu_caps:
                w(f"| {c.cluster} | {_pct(c.min_cap_ratio)} | {_pct(c.capped_time_ratio)} |")
            w("")

    w("## 資料可用性\n")
    w("| 資料來源 | 有無 |")
    w("|---|---|")
    for k, v in m.meta.data_sources.items():
        w(f"| {k} | {'有' if v else '無'} |")
    if m.meta.ftrace_setup_errors:
        w("\nftrace 啟用失敗：" + "；".join(f"`{e}`" for e in m.meta.ftrace_setup_errors))
    a = m.sidecar_alignment
    if a.available:
        w(f"\nsidecar 時間對齊：吻合率 {_pct(a.match_ratio)}（平移 ±1–2 s 的基準 {_pct(a.shifted_baseline_ratio)}）")
    w("")
    return "\n".join(out)
