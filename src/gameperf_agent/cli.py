"""Command-line entry point: `gpa record`, `gpa metrics`, `gpa analyze`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_record(args: argparse.Namespace) -> int:
    from gameperf_agent.record import RecordError, record

    try:
        record(args.out, duration_s=args.duration, config=args.config, sidecar=not args.no_sidecar,
               adb_path=args.adb, serial=args.serial)
    except RecordError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_metrics(args: argparse.Namespace) -> int:
    from gameperf_agent.analyze import analyze_trace

    payload = analyze_trace(args.trace, sidecar_path=args.sidecar, package=args.package).model_dump_json(indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    from gameperf_agent import grounding, report, rules
    from gameperf_agent.analyze import analyze_trace

    metrics = analyze_trace(args.trace, sidecar_path=args.sidecar, package=args.package)
    rule = rules.classify(metrics)
    llm_result = llm_grounding = None
    if not args.no_llm:
        from gameperf_agent import llm

        llm_result = llm.interpret(metrics, model=args.model, effort=args.effort)
        if llm_result.verdict:
            llm_grounding = grounding.check(llm_result.verdict, metrics)
        else:
            print(f"warning: LLM verdict unavailable: {llm_result.error}", file=sys.stderr)

    md = report.render(metrics, rule, grounding.check(rule, metrics), llm_result, llm_grounding)
    out = Path(args.out) if args.out else Path(args.trace).with_suffix(".report.md")
    out.write_text(md, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    from gameperf_agent.llm import DEFAULT_MODEL

    p = argparse.ArgumentParser(prog="gpa", description="Perfetto trace triage for mobile-game performance.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="record a trace + sidecar from a USB-connected device")
    r.add_argument("out", help="output path without extension, e.g. traces/mygame_high")
    r.add_argument("--duration", type=int, default=30, help="seconds (default 30)")
    r.add_argument("--config", default=str(Path(__file__).resolve().parents[2] / "configs" / "game.pbtx"))
    r.add_argument("--no-sidecar", action="store_true", help="trace only (to measure sampler overhead)")
    r.add_argument("--adb", help="path to adb (default: PATH, ANDROID_HOME, %%LOCALAPPDATA%%\\Android\\Sdk)")
    r.add_argument("--serial", help="device serial when several are connected")
    r.set_defaults(func=_cmd_record)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("trace")
        sp.add_argument("--sidecar", help="sidecar CSV (default: <trace name>.sidecar.csv if present)")
        sp.add_argument("--package", help="game package name (default: process with most frames)")

    mt = sub.add_parser("metrics", help="extract metrics JSON only (deterministic, no LLM)")
    common(mt)
    mt.add_argument("--out", help="write JSON here instead of stdout")
    mt.set_defaults(func=_cmd_metrics)

    a = sub.add_parser("analyze", help="metrics + rule baseline + LLM -> Markdown report")
    common(a)
    a.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default {DEFAULT_MODEL})")
    a.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="output_config.effort")
    a.add_argument("--no-llm", action="store_true", help="rule baseline only (no API call)")
    a.add_argument("--out", help="report path (default: <trace>.report.md)")
    a.add_argument("--json", help="also write the metrics JSON here")
    a.set_defaults(func=_cmd_analyze)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
