"""Paired benchmark runner for EXP-2.

Runs filtered/unfiltered trials with randomized ordering to control for
prompt-cache state and network conditions.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from tcp.agent.loop import LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.agent.tasks import AgentTask, build_agent_tasks


@dataclass(frozen=True)
class PairedTrial:
    """One paired filtered/unfiltered comparison for a single task."""

    task_name: str
    unfiltered: LoopMetrics
    filtered: LoopMetrics

    @property
    def latency_delta_ms(self) -> float:
        """Positive means unfiltered was slower (filtered wins)."""
        return self.unfiltered.total_response_time_ms - self.filtered.total_response_time_ms

    @property
    def token_delta(self) -> int:
        """Positive means unfiltered used more tokens (filtered wins)."""
        return self.unfiltered.input_tokens - self.filtered.input_tokens


@dataclass(frozen=True)
class BenchmarkReport:
    """Complete benchmark report with per-trial data and summary."""

    trials: tuple[PairedTrial, ...]
    summary: dict[str, float | int]

    @classmethod
    def from_trials(cls, trials: list[PairedTrial]) -> BenchmarkReport:
        """Compute summary statistics from a list of trials."""
        if not trials:
            return cls(trials=(), summary={"trial_count": 0})

        latency_deltas = [t.latency_delta_ms for t in trials]
        token_deltas = [t.token_delta for t in trials]
        filtered_correct = sum(1 for t in trials if t.filtered.selected_tool_correct)
        unfiltered_correct = sum(
            1 for t in trials if t.unfiltered.selected_tool_correct
        )
        n = len(trials)

        return cls(
            trials=tuple(trials),
            summary={
                "trial_count": n,
                "mean_latency_delta_ms": sum(latency_deltas) / n,
                "mean_token_delta": sum(token_deltas) / n,
                "min_latency_delta_ms": min(latency_deltas),
                "max_latency_delta_ms": max(latency_deltas),
                "filtered_correct_rate": filtered_correct / n,
                "unfiltered_correct_rate": unfiltered_correct / n,
                "total_filtered_input_tokens": sum(
                    t.filtered.input_tokens for t in trials
                ),
                "total_unfiltered_input_tokens": sum(
                    t.unfiltered.input_tokens for t in trials
                ),
            },
        )


def build_filtered_schemas(
    tasks: list[AgentTask],
    corpus_schemas: list[dict],
    *,
    network: bool = False,
) -> dict[str, list[dict]]:
    """Build per-task filtered schema subsets using TCP gating.

    Uses bitmask filtering with the given environment.  Each task gets
    the bitmask survivors as its filtered set.

    Args:
        network: If True, allow network tools (online environment).
                 If False, deny them (offline environment).
    """
    from tcp.core.descriptors import CapabilityFlags
    from tcp.harness.bitmask_filter import EnvironmentMask, bitmask_filter
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.normalize import normalize_capability_descriptor

    # Build ToolRecords from corpus
    entries = build_mcp_corpus()
    records = []
    for entry in entries:
        records.append(normalize_capability_descriptor(entry.descriptor))

    # Build schema lookup by tool name
    schema_by_name: dict[str, dict] = {s["name"]: s for s in corpus_schemas}

    deny = EnvironmentMask.from_constraints(network=network)
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    result = bitmask_filter(records, deny_mask=deny, approval_mask=approval)
    survivor_names = frozenset(r.tool_name for r in result.survivors)

    filtered_schemas = [
        schema_by_name[name] for name in survivor_names if name in schema_by_name
    ]

    return {task.name: filtered_schemas for task in tasks}


def _metrics_to_dict(m: LoopMetrics) -> dict:
    """Serialize LoopMetrics to a JSON-safe dict."""
    return {
        "task_name": m.task_name,
        "tool_count": m.tool_count,
        "turns": m.turns,
        "first_token_latency_ms": m.first_token_latency_ms,
        "total_response_time_ms": m.total_response_time_ms,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "tools_called": list(m.tools_called),
        "selected_tool_correct": m.selected_tool_correct,
        "error": m.error,
        "error_kind": m.error_kind,
    }


def _trial_to_dict(t: PairedTrial) -> dict:
    """Serialize a PairedTrial to a JSON-safe dict."""
    return {
        "task_name": t.task_name,
        "unfiltered": _metrics_to_dict(t.unfiltered),
        "filtered": _metrics_to_dict(t.filtered),
        "latency_delta_ms": t.latency_delta_ms,
        "token_delta": t.token_delta,
    }


def _save_incremental(trials: list[PairedTrial], path: Path) -> None:
    """Atomically write all trials collected so far to JSON."""
    tmp = path.with_suffix(".tmp")
    data = {"trials": [_trial_to_dict(t) for t in trials]}
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


async def run_paired_benchmark(
    tasks: list[AgentTask],
    corpus_schemas: list[dict],
    filtered_schemas_by_task: dict[str, list[dict]],
    *,
    repetitions: int = 5,
    model: str = "claude-sonnet-4-6",
    results_path: Path | None = None,
) -> BenchmarkReport:
    """Run paired filtered/unfiltered trials for each task.

    Order is randomized per pair to control for prompt caching.
    If results_path is provided, saves incrementally after each trial.
    """
    mock_exec = get_mock_executor()
    all_trials: list[PairedTrial] = []

    for task in tasks:
        filtered_schemas = filtered_schemas_by_task[task.name]

        for _rep in range(repetitions):
            run_filtered_first = random.random() < 0.5

            async def _run_arm(schemas: list[dict]) -> LoopMetrics:
                return await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                )

            if run_filtered_first:
                filtered_metrics = await _run_arm(filtered_schemas)
                unfiltered_metrics = await _run_arm(corpus_schemas)
            else:
                unfiltered_metrics = await _run_arm(corpus_schemas)
                filtered_metrics = await _run_arm(filtered_schemas)

            trial = PairedTrial(
                task_name=task.name,
                unfiltered=unfiltered_metrics,
                filtered=filtered_metrics,
            )
            all_trials.append(trial)

            if results_path is not None:
                _save_incremental(all_trials, results_path)

    return BenchmarkReport.from_trials(all_trials)


@dataclass(frozen=True)
class SmokeResult:
    """Result of a single-call smoke test."""

    trial: PairedTrial
    passed: bool
    issues: tuple[str, ...]


async def run_smoke_test(
    *,
    model: str = "claude-sonnet-4-6",
) -> SmokeResult:
    """Run 1 task x 1 rep to validate the full pipeline. ~$0.10."""
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    tasks = build_agent_tasks()
    # Pick first task with a non-None expected tool
    task = next(t for t in tasks if t.expected_tool is not None)

    entries = build_mcp_corpus()
    corpus_schemas = corpus_to_anthropic_schemas(entries)
    filtered = build_filtered_schemas([task], corpus_schemas)

    report = await run_paired_benchmark(
        tasks=[task],
        corpus_schemas=corpus_schemas,
        filtered_schemas_by_task=filtered,
        repetitions=1,
        model=model,
    )

    trial = report.trials[0]
    issues: list[str] = []

    for arm_name, metrics in [("unfiltered", trial.unfiltered), ("filtered", trial.filtered)]:
        if metrics.error is not None:
            issues.append(f"{arm_name}: error={metrics.error} (kind={metrics.error_kind})")
        if metrics.turns == 0:
            issues.append(f"{arm_name}: zero turns")
        if metrics.input_tokens == 0:
            issues.append(f"{arm_name}: zero input tokens")

    return SmokeResult(
        trial=trial,
        passed=len(issues) == 0,
        issues=tuple(issues),
    )


@dataclass(frozen=True)
class MatrixCell:
    """One cell of the generalization matrix."""

    model: str
    environment: str  # "offline" or "online"
    cache: str  # "cold" or "warm"
    report: BenchmarkReport


@dataclass(frozen=True)
class MatrixReport:
    """Full generalization matrix results."""

    cells: tuple[MatrixCell, ...]

    def summary_table(self) -> str:
        """Human-readable summary of each cell."""
        lines = [
            f"{'Model':<25} {'Env':<10} {'Cache':<6} "
            f"{'Trials':>7} {'TokΔ':>8} {'F-Corr':>7} {'U-Corr':>7} {'LatΔ':>10}"
        ]
        lines.append("-" * 85)
        for c in self.cells:
            s = c.report.summary
            n = s["trial_count"]
            if n == 0:
                lines.append(f"{c.model:<25} {c.environment:<10} {c.cache:<6} {'(empty)':>7}")
                continue
            lines.append(
                f"{c.model:<25} {c.environment:<10} {c.cache:<6} "
                f"{n:>7} {s['mean_token_delta']:>+8.0f} "
                f"{s['filtered_correct_rate']:>6.0%} "
                f"{s['unfiltered_correct_rate']:>6.0%} "
                f"{s['mean_latency_delta_ms']:>+9.0f}ms"
            )
        return "\n".join(lines)


async def run_matrix_benchmark(
    *,
    models: list[str],
    environments: list[str],
    repetitions: int = 3,
    results_path: Path | None = None,
) -> MatrixReport:
    """Run the generalization matrix across models and environments.

    Cache control: each (model, environment) pair runs twice — once
    cold (first run) and once warm (immediate re-run with same schemas,
    benefiting from Anthropic's prompt caching).
    """
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    tasks = build_agent_tasks()
    entries = build_mcp_corpus()
    corpus_schemas = corpus_to_anthropic_schemas(entries)

    cells: list[MatrixCell] = []
    all_trials: list[dict] = []  # for incremental persistence

    for model in models:
        for env in environments:
            network = env == "online"
            filtered = build_filtered_schemas(tasks, corpus_schemas, network=network)

            for cache_label in ("cold", "warm"):
                print(f"\n  [{model}] [{env}] [{cache_label}] "
                      f"— {len(tasks)} tasks x {repetitions} reps...")

                report = await run_paired_benchmark(
                    tasks=tasks,
                    corpus_schemas=corpus_schemas,
                    filtered_schemas_by_task=filtered,
                    repetitions=repetitions,
                    model=model,
                )

                cell = MatrixCell(
                    model=model,
                    environment=env,
                    cache=cache_label,
                    report=report,
                )
                cells.append(cell)

                # Incremental save
                if results_path is not None:
                    cell_data = {
                        "model": cell.model,
                        "environment": cell.environment,
                        "cache": cell.cache,
                        "summary": cell.report.summary,
                        "trials": [_trial_to_dict(t) for t in cell.report.trials],
                    }
                    all_trials.append(cell_data)
                    tmp = results_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"cells": all_trials}, indent=2))
                    os.replace(tmp, results_path)

    return MatrixReport(cells=tuple(cells))
