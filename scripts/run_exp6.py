#!/usr/bin/env python
"""EXP-6 runner — primacy-bias experiment on the adversarial ambiguous-lane corpus.

Usage:
    poetry run python scripts/run_exp6.py
    poetry run python scripts/run_exp6.py --model claude-sonnet-4-6 --repetitions 3
    poetry run python scripts/run_exp6.py --results-path exp6-results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TCP-EXP-6: Primacy-bias experiment"
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Anthropic model to use (default: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Rounds per (task, ordering) combination (default: 1)",
    )
    parser.add_argument(
        "--results-path",
        type=Path,
        default=Path("exp6-results.json"),
        help="Output path for JSON results (default: exp6-results.json)",
    )
    args = parser.parse_args()

    from tcp.agent.exp6 import run_exp6

    print(f"\nTCP-EXP-6: Primacy-Bias Experiment")
    print(f"  model       : {args.model}")
    print(f"  repetitions : {args.repetitions}")
    print(f"  results_path: {args.results_path}")
    print()

    report = asyncio.run(
        run_exp6(
            model=args.model,
            repetitions=args.repetitions,
            results_path=args.results_path,
        )
    )

    print("\n" + "=" * 80)
    print("RESULTS TABLE")
    print("=" * 80)
    print(report.summary_table())

    print("\n" + "=" * 80)
    print("PRIMACY BIAS SUMMARY")
    print("=" * 80)
    bias = report.primacy_bias_summary()

    print(f"  Tasks evaluated       : {bias['n_tasks']}")
    print(f"  Total trials          : {bias['n_total_trials']}")
    print(f"  Model                 : {bias['model']}")
    print()
    print("  Condition: correct-FIRST")
    cf = bias["correct_first"]
    print(f"    first-tool correctness : {cf['first_tool_correctness']:.1%}")
    print(f"    any-position correct   : {cf['any_position_correctness']:.1%}")
    print(f"    mean input tokens      : {cf['mean_input_tokens']:.0f}")
    print(f"    mean latency ms        : {cf['mean_latency_ms']:.0f}")
    print(f"    error rate             : {cf['error_rate']:.1%}")
    print()
    print("  Condition: correct-NOT-FIRST (middle + last)")
    cn = bias["correct_not_first"]
    print(f"    first-tool correctness : {cn['first_tool_correctness']:.1%}")
    print(f"    any-position correct   : {cn['any_position_correctness']:.1%}")
    print(f"    mean input tokens      : {cn['mean_input_tokens']:.0f}")
    print(f"    mean latency ms        : {cn['mean_latency_ms']:.0f}")
    print(f"    error rate             : {cn['error_rate']:.1%}")
    print()
    print(f"  Delta first-tool correctness  : {bias['delta_first_tool_correctness']:+.1%}")
    print(f"  Delta any-position correctness: {bias['delta_any_position_correctness']:+.1%}")
    print()
    if bias["stop_condition_met"]:
        print("  [STOP CONDITION] any-position delta < 1pp — ordering has no material effect")
    else:
        print("  [RESULT] any-position delta ≥ 1pp — primacy bias detected")

    # Write final summary to results file
    if args.results_path:
        existing = {}
        if args.results_path.exists():
            try:
                existing = json.loads(args.results_path.read_text())
            except Exception:
                pass
        existing["primacy_bias_summary"] = bias
        args.results_path.write_text(json.dumps(existing, indent=2))
        print(f"\n  Results saved to: {args.results_path}")


if __name__ == "__main__":
    main()
