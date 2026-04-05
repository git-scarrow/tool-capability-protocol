"""CLI for EXP-2 benchmark apparatus.

Usage:
    python -m tcp.agent --preflight          # offline checks only ($0)
    python -m tcp.agent --smoke              # 1 API call (~$0.10)
    python -m tcp.agent --run                # full benchmark (~$30)
    python -m tcp.agent --run --reps 3       # fewer reps (~$18)
    python -m tcp.agent --run --output out.json
    python -m tcp.agent --matrix --output matrix.json  # generalization matrix
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tcp.agent",
        description="EXP-2 TCP-gated agent loop benchmark",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="Run offline pre-flight checks only ($0)",
    )
    mode.add_argument(
        "--smoke",
        action="store_true",
        help="Run 1 task x 1 rep against real API (~$0.10)",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="Run full paired benchmark (~$30)",
    )
    mode.add_argument(
        "--matrix",
        action="store_true",
        help="Run generalization matrix across models + environments",
    )
    mode.add_argument(
        "--ablation",
        action="store_true",
        help="Run 3-arm adversarial ablation (ungated/fixed/per-task)",
    )
    mode.add_argument(
        "--scale",
        action="store_true",
        help="Run scale stress test with 500+ tool corpus",
    )
    mode.add_argument(
        "--layered",
        action="store_true",
        help="Run layered benchmark (deterministic bypass + ambiguous LLM)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=5,
        help="Repetitions per task (default: 5)",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Model to benchmark (default: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for incremental JSON results",
    )
    args = parser.parse_args()

    if args.preflight:
        _cmd_preflight()
    elif args.smoke:
        if not _cmd_preflight():
            sys.exit(1)
        asyncio.run(_cmd_smoke(args.model))
    elif args.run:
        if not _cmd_preflight():
            sys.exit(1)
        asyncio.run(_cmd_run(args.reps, args.model, args.output))
    elif args.matrix:
        if not _cmd_preflight():
            sys.exit(1)
        asyncio.run(_cmd_matrix(args.reps, args.output))
    elif args.ablation:
        if not _cmd_preflight():
            sys.exit(1)
        asyncio.run(_cmd_ablation(args.reps, args.model, args.output))
    elif args.scale:
        if not _cmd_preflight():
            sys.exit(1)
        asyncio.run(_cmd_scale(args.reps, args.model, args.output))
    elif args.layered:
        if not _cmd_preflight():
            sys.exit(1)
        asyncio.run(_cmd_layered(args.reps, args.model, args.output))


def _cmd_preflight() -> bool:
    """Run offline checks. Returns True if all pass."""
    from tcp.agent.preflight import run_preflight

    report = run_preflight()
    print(report.summary())
    return report.passed


async def _cmd_smoke(model: str) -> None:
    """Run 1 API call to validate pipeline."""
    from tcp.agent.benchmark import run_smoke_test

    print("\n--- Smoke test (1 task x 1 rep) ---")
    result = await run_smoke_test(model=model)

    trial = result.trial
    print(f"Task: {trial.task_name}")
    for arm_name, m in [("unfiltered", trial.unfiltered), ("filtered", trial.filtered)]:
        print(
            f"  {arm_name}: {m.turns} turns, "
            f"{m.input_tokens} in_tok, {m.output_tokens} out_tok, "
            f"{m.total_response_time_ms:.0f}ms, "
            f"tools={list(m.tools_called)}, "
            f"correct={m.selected_tool_correct}"
        )
        if m.error:
            print(f"    ERROR [{m.error_kind}]: {m.error}")

    if result.passed:
        print("\nSmoke test PASSED")
    else:
        print(f"\nSmoke test FAILED:")
        for issue in result.issues:
            print(f"  - {issue}")
        sys.exit(1)


async def _cmd_run(reps: int, model: str, output: Path | None) -> None:
    """Run the full benchmark."""
    from tcp.agent.benchmark import build_filtered_schemas, run_paired_benchmark
    from tcp.agent.tasks import build_agent_tasks
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    tasks = build_agent_tasks()
    entries = build_mcp_corpus()
    corpus_schemas = corpus_to_anthropic_schemas(entries)
    filtered = build_filtered_schemas(tasks, corpus_schemas)

    total_calls = len(tasks) * reps * 2
    print(f"\n--- Full benchmark: {len(tasks)} tasks x {reps} reps x 2 arms = {total_calls} API calls ---")
    if output:
        print(f"Results will be saved incrementally to {output}")

    report = await run_paired_benchmark(
        tasks=tasks,
        corpus_schemas=corpus_schemas,
        filtered_schemas_by_task=filtered,
        repetitions=reps,
        model=model,
        results_path=output,
    )

    print(f"\n--- Results ({report.summary['trial_count']} trials) ---")
    for key, val in report.summary.items():
        if isinstance(val, float):
            print(f"  {key}: {val:.2f}")
        else:
            print(f"  {key}: {val}")

    # Check for errors
    errors = [t for t in report.trials if t.filtered.error or t.unfiltered.error]
    if errors:
        print(f"\n{len(errors)} trials had errors:")
        for t in errors[:5]:
            for arm, m in [("F", t.filtered), ("U", t.unfiltered)]:
                if m.error:
                    print(f"  [{arm}] {t.task_name}: [{m.error_kind}] {m.error[:80]}")


async def _cmd_matrix(reps: int, output: Path | None) -> None:
    """Run the generalization matrix."""
    from tcp.agent.benchmark import run_matrix_benchmark

    models = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
    environments = ["offline", "online"]

    total_cells = len(models) * len(environments) * 2  # x2 for cold/warm
    calls_per_cell = 12 * reps * 2
    total_calls = total_cells * calls_per_cell
    print(
        f"\n--- Matrix benchmark ---"
        f"\n  Models: {models}"
        f"\n  Environments: {environments}"
        f"\n  Cache: cold + warm per cell"
        f"\n  Reps per cell: {reps}"
        f"\n  Total cells: {total_cells}"
        f"\n  Total API calls: {total_calls}"
    )
    if output:
        print(f"  Results: {output}")

    report = await run_matrix_benchmark(
        models=models,
        environments=environments,
        repetitions=reps,
        results_path=output,
    )

    print(f"\n{report.summary_table()}")


async def _cmd_ablation(reps: int, model: str, output: Path | None) -> None:
    """Run the 3-arm adversarial ablation."""
    from tcp.agent.adversarial import build_adversarial_tasks
    from tcp.agent.benchmark import run_adversarial_ablation

    adv_tasks = build_adversarial_tasks()
    total_calls = len(adv_tasks) * reps * 3
    print(
        f"\n--- Adversarial ablation ---"
        f"\n  Tasks: {len(adv_tasks)}"
        f"\n  Arms: ungated / fixed-filter / per-task-filter"
        f"\n  Reps: {reps}"
        f"\n  Model: {model}"
        f"\n  Total API calls: {total_calls}"
    )
    if output:
        print(f"  Results: {output}")

    print("\nFilter sizes:")
    report = await run_adversarial_ablation(
        repetitions=reps,
        model=model,
        results_path=output,
    )

    print(f"\n{report.summary_table()}")

    # Check for errors
    errors = []
    for t in report.trials:
        for arm_name, m in [("U", t.ungated), ("F", t.fixed_filter), ("PT", t.per_task_filter)]:
            if m.error:
                errors.append(f"  [{arm_name}] {t.task_name}: [{m.error_kind}] {m.error[:80]}")
    if errors:
        print(f"\n{len(errors)} arm errors:")
        for e in errors[:10]:
            print(e)


async def _cmd_scale(reps: int, model: str, output: Path | None) -> None:
    """Run scale stress test with 500+ tool corpus."""
    from tcp.agent.benchmark import build_filtered_schemas, run_paired_benchmark
    from tcp.agent.mock_executors import MOCK_RESPONSES
    from tcp.agent.synthetic_corpus import build_scaled_corpus
    from tcp.agent.tasks import build_agent_tasks
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    tasks = build_agent_tasks()
    entries = build_scaled_corpus()
    corpus_schemas = corpus_to_anthropic_schemas(entries)

    # Ensure mock executor covers synthetic tools (default response is fine)
    filtered = build_filtered_schemas(tasks, corpus_schemas)

    total_calls = len(tasks) * reps * 2
    print(
        f"\n--- Scale stress test ---"
        f"\n  Corpus: {len(entries)} tools ({len(corpus_schemas)} schemas)"
        f"\n  Tasks: {len(tasks)}"
        f"\n  Reps: {reps}"
        f"\n  Model: {model}"
        f"\n  Total API calls: {total_calls}"
    )

    # Show per-task filter sizes
    print("\nPer-task filter sizes:")
    for task in tasks:
        n = len(filtered[task.name])
        print(f"  {task.name}: {n}/{len(corpus_schemas)}")

    if output:
        print(f"\nResults: {output}")

    report = await run_paired_benchmark(
        tasks=tasks,
        corpus_schemas=corpus_schemas,
        filtered_schemas_by_task=filtered,
        repetitions=reps,
        model=model,
        results_path=output,
    )

    print(f"\n--- Results ({report.summary['trial_count']} trials) ---")
    for key, val in report.summary.items():
        if isinstance(val, float):
            print(f"  {key}: {val:.2f}")
        else:
            print(f"  {key}: {val}")

    errors = [t for t in report.trials if t.filtered.error or t.unfiltered.error]
    if errors:
        print(f"\n{len(errors)} trials had errors:")
        for t in errors[:5]:
            for arm, m in [("F", t.filtered), ("U", t.unfiltered)]:
                if m.error:
                    print(f"  [{arm}] {t.task_name}: [{m.error_kind}] {m.error[:80]}")


async def _cmd_layered(reps: int, model: str, output: Path | None) -> None:
    """Run the layered benchmark."""
    from tcp.agent.benchmark import run_layered_benchmark

    print(
        f"\n--- Layered benchmark ---"
        f"\n  Reps: {reps}"
        f"\n  Model: {model}"
    )
    if output:
        print(f"  Results: {output}")

    report = await run_layered_benchmark(
        repetitions=reps,
        model=model,
        results_path=output,
    )

    print(f"\n{report.summary_table()}")
