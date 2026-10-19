"""Runs the eval: every labeled trace -> metrics -> rule verdict + N LLM verdicts -> agreement stats.

Usage:
    uv run python eval/run_eval.py                    # rules + LLM (3 runs each)
    uv run python eval/run_eval.py --no-llm           # rules only, no API cost
    uv run python eval/run_eval.py --runs 5 --model claude-opus-5-5

Raw LLM responses are cached in eval/results/raw/ (git-ignored) so re-running
only calls the API for missing runs; pass --refresh to force new calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gameperf_agent import grounding, rules  # noqa: E402
from gameperf_agent.analyze import analyze_trace  # noqa: E402
from gameperf_agent.evaluation import TraceResult, load_labels, majority, render_summary, summarize, write_json  # noqa: E402
from gameperf_agent.llm import DEFAULT_MODEL, interpret  # noqa: E402
from gameperf_agent.verdict import Verdict  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", default=str(ROOT / "eval" / "labels.yaml"))
    p.add_argument("--out", default=str(ROOT / "eval" / "results"))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default=None)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--refresh", action="store_true", help="ignore cached LLM responses")
    args = p.parse_args()

    out = Path(args.out)
    labeled, pending = load_labels(args.labels)
    results: list[TraceResult] = []
    for item in labeled:
        trace = ROOT / item.trace
        if not trace.exists():
            print(f"skip {item.id}: {trace} not found", file=sys.stderr)
            continue
        metrics = analyze_trace(trace, sidecar_path=(ROOT / item.sidecar) if item.sidecar else None,
                                package=item.package)
        write_json(out / "metrics" / f"{item.id}.json", metrics.model_dump(mode="json"))
        rule = rules.classify(metrics)
        r = TraceResult(id=item.id, human=item.label, human_reason=item.reason, rule=rule.category)

        if not args.no_llm:
            for i in range(args.runs):
                cache = out / "raw" / f"{item.id}.{args.model}.{i}.json"
                if cache.exists() and not args.refresh:
                    saved = json.loads(cache.read_text(encoding="utf-8"))
                else:
                    res = interpret(metrics, model=args.model, effort=args.effort)
                    saved = {"verdict": res.verdict.model_dump() if res.verdict else None, "error": res.error,
                             "model_served": res.model_served, "stop_reason": res.stop_reason,
                             "usage": {"input": res.input_tokens, "output": res.output_tokens}}
                    write_json(cache, saved)
                verdict = Verdict.model_validate(saved["verdict"]) if saved["verdict"] else None
                r.llm_runs.append(verdict.category if verdict else None)
                if saved.get("error"):
                    r.llm_errors.append(saved["error"])
                if verdict:
                    g = grounding.check(verdict, metrics)
                    r.grounding_passed += g.passed
                    r.grounding_total += len(g.checks)
            r.llm_majority, r.llm_stability = majority(r.llm_runs)
            if r.llm_majority and r.llm_majority != r.human:
                first = next(json.loads((out / "raw" / f"{item.id}.{args.model}.{i}.json").read_text(encoding="utf-8"))
                             for i in range(args.runs))
                if first["verdict"]:
                    r.llm_top_evidence = [f"{e['claim']} (`{e['metric_path']}` = {e['value']})"
                                          for e in first["verdict"]["evidence"][:3]]
        results.append(r)
        print(f"{item.id}: human={r.human} rule={r.rule} llm={r.llm_majority} runs={r.llm_runs}")

    summary = summarize(results, pending, None if args.no_llm else args.model, 0 if args.no_llm else args.runs)
    write_json(out / "results.json", {"summary": summary, "traces": [r.__dict__ for r in results]})
    (out / "summary.md").write_text(render_summary(summary, results, pending), encoding="utf-8")
    print(f"wrote {out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
