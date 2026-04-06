"""Paired benchmark runner for EXP-2.

Runs filtered/unfiltered trials with randomized ordering to control for
prompt-cache state and network conditions.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable

from tcp.agent.ambiguous_tasks import build_ambiguous_tasks
from tcp.agent.lane_report import LaneReport, build_lane_report
from tcp.agent.loop import LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.agent.routing_strategy import should_bypass_llm
from tcp.agent.tasks import AgentTask, build_agent_tasks
from tcp.harness.router import RouteConfidence, RouteResult


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


def build_fixed_filtered_schemas(
    tasks: list[AgentTask],
    corpus_schemas: list[dict],
    *,
    network: bool = False,
) -> dict[str, list[dict]]:
    """Build filtered schemas using fixed bitmask filtering (benchmark control).

    Every task gets the same set of bitmask survivors.
    """
    from tcp.core.descriptors import CapabilityFlags
    from tcp.harness.bitmask_filter import EnvironmentMask, bitmask_filter
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.normalize import normalize_capability_descriptor

    entries = build_mcp_corpus()
    records = [normalize_capability_descriptor(e.descriptor) for e in entries]
    schema_by_name: dict[str, dict] = {s["name"]: s for s in corpus_schemas}

    deny = EnvironmentMask.from_constraints(network=network)
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    result = bitmask_filter(records, deny_mask=deny, approval_mask=approval)
    survivor_names = frozenset(r.tool_name for r in result.survivors)

    filtered_schemas = [
        schema_by_name[name] for name in survivor_names if name in schema_by_name
    ]

    return {task.name: filtered_schemas for task in tasks}


def build_filtered_schemas(
    tasks: list[AgentTask],
    corpus_schemas: list[dict],
    *,
    network: bool = False,
    mode: str = "per_task",
) -> dict[str, list[dict]]:
    """Build filtered schema subsets for each task.

    Args:
        mode: Filtering strategy.
            "per_task" (default) — full gate_tools pipeline per task.
            "fixed" — static bitmask filtering (all tasks get same set).
    """
    if mode == "fixed":
        return build_fixed_filtered_schemas(tasks, corpus_schemas, network=network)

    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.gating import RuntimeEnvironment, gate_tools
    from tcp.harness.models import ToolSelectionRequest
    from tcp.harness.normalize import normalize_capability_descriptor

    entries = build_mcp_corpus()
    records = [normalize_capability_descriptor(e.descriptor) for e in entries]
    schema_by_name: dict[str, dict] = {s["name"]: s for s in corpus_schemas}

    all_names = frozenset(e.descriptor.name for e in entries)
    env = RuntimeEnvironment(
        network_enabled=network,
        file_access_enabled=True,
        stdin_enabled=True,
        installed_tools=all_names,
    )

    # Default selection request for tasks without one
    default_request = ToolSelectionRequest.from_kwargs(
        preferred_criteria="speed",
        require_auto_approval=False,
    )

    result: dict[str, list[dict]] = {}
    for task in tasks:
        request = task.selection_request or default_request
        gate_result = gate_tools(records, request, env)
        survivor_names = {t.tool_name for t in gate_result.approved_tools}
        survivor_names |= {t.tool_name for t in gate_result.approval_required_tools}
        schemas = [schema_by_name[n] for n in survivor_names if n in schema_by_name]
        result[task.name] = schemas

    return result


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
        "expected_tool_any_position": m.expected_tool_any_position,
        "error": m.error,
        "error_kind": m.error_kind,
        "llm_bypassed": m.llm_bypassed,
        "route_confidence": m.route_confidence,
        "survivor_count": m.survivor_count,
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


# --- Three-arm adversarial ablation (MT-6) ---


@dataclass(frozen=True)
class ThreeArmTrial:
    """One trial comparing ungated, fixed-filter, and per-task-filter arms."""

    task_name: str
    ungated: LoopMetrics
    fixed_filter: LoopMetrics
    per_task_filter: LoopMetrics


@dataclass(frozen=True)
class AblationReport:
    """Results of the three-arm adversarial ablation."""

    trials: tuple[ThreeArmTrial, ...]

    def summary_table(self) -> str:
        from collections import defaultdict

        by_task: dict[str, list[ThreeArmTrial]] = defaultdict(list)
        for t in self.trials:
            by_task[t.task_name].append(t)

        lines = [
            f"{'Task':<35} {'U-Corr':>7} {'F-Corr':>7} {'PT-Corr':>8} "
            f"{'U-Tok':>7} {'F-Tok':>7} {'PT-Tok':>7}"
        ]
        lines.append("-" * 85)

        totals = {"u_c": 0, "f_c": 0, "pt_c": 0, "n": 0}
        for name, trials in by_task.items():
            n = len(trials)
            u_c = sum(1 for t in trials if t.ungated.selected_tool_correct) / n
            f_c = sum(1 for t in trials if t.fixed_filter.selected_tool_correct) / n
            pt_c = sum(1 for t in trials if t.per_task_filter.selected_tool_correct) / n
            u_tok = sum(t.ungated.input_tokens for t in trials) // n
            f_tok = sum(t.fixed_filter.input_tokens for t in trials) // n
            pt_tok = sum(t.per_task_filter.input_tokens for t in trials) // n
            lines.append(
                f"{name:<35} {u_c:>6.0%} {f_c:>6.0%} {pt_c:>7.0%} "
                f"{u_tok:>7} {f_tok:>7} {pt_tok:>7}"
            )
            totals["u_c"] += sum(1 for t in trials if t.ungated.selected_tool_correct)
            totals["f_c"] += sum(1 for t in trials if t.fixed_filter.selected_tool_correct)
            totals["pt_c"] += sum(1 for t in trials if t.per_task_filter.selected_tool_correct)
            totals["n"] += n

        lines.append("-" * 85)
        n = totals["n"]
        lines.append(
            f"{'TOTAL':<35} {totals['u_c']/n:>6.0%} {totals['f_c']/n:>6.0%} "
            f"{totals['pt_c']/n:>7.0%}"
        )
        return "\n".join(lines)


def build_per_task_filtered_schemas(
    adv_tasks: list,
    corpus_schemas: list[dict],
) -> dict[str, list[dict]]:
    """Build per-task filtered schemas for adversarial tasks.

    Delegates to the common per-task filtering path via
    build_filtered_schemas(mode="per_task").
    """
    # Convert AdversarialTask list to AgentTask list with selection_request
    agent_tasks = []
    for at in adv_tasks:
        agent_tasks.append(
            AgentTask(
                name=at.agent_task.name,
                prompt=at.agent_task.prompt,
                expected_tool=at.agent_task.expected_tool,
                selection_request=at.selection_request,
            )
        )
    return build_filtered_schemas(agent_tasks, corpus_schemas, mode="per_task")


def _three_arm_trial_to_dict(t: ThreeArmTrial) -> dict:
    return {
        "task_name": t.task_name,
        "ungated": _metrics_to_dict(t.ungated),
        "fixed_filter": _metrics_to_dict(t.fixed_filter),
        "per_task_filter": _metrics_to_dict(t.per_task_filter),
    }


async def run_adversarial_ablation(
    *,
    repetitions: int = 3,
    model: str = "claude-sonnet-4-6",
    results_path: Path | None = None,
) -> AblationReport:
    """Run the 3-arm adversarial ablation benchmark."""
    from tcp.agent.adversarial import build_adversarial_tasks
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    adv_tasks = build_adversarial_tasks()
    entries = build_mcp_corpus()
    corpus_schemas = corpus_to_anthropic_schemas(entries)

    # Build the two filtered arms
    tasks_for_fixed = [at.agent_task for at in adv_tasks]
    fixed_filtered = build_filtered_schemas(tasks_for_fixed, corpus_schemas, network=False)
    per_task_filtered = build_per_task_filtered_schemas(adv_tasks, corpus_schemas)

    # Log filter sizes
    for at in adv_tasks:
        name = at.agent_task.name
        f_n = len(fixed_filtered[name])
        pt_n = len(per_task_filtered[name])
        print(f"  {name}: ungated={len(corpus_schemas)}, fixed={f_n}, per-task={pt_n}")

    mock_exec = get_mock_executor()
    all_trials: list[ThreeArmTrial] = []

    for at in adv_tasks:
        task = at.agent_task
        fixed_schemas = fixed_filtered[task.name]
        pt_schemas = per_task_filtered[task.name]

        for _rep in range(repetitions):
            # Randomize arm order
            arms = ["ungated", "fixed", "per_task"]
            random.shuffle(arms)

            results: dict[str, LoopMetrics] = {}
            for arm in arms:
                if arm == "ungated":
                    schemas = corpus_schemas
                elif arm == "fixed":
                    schemas = fixed_schemas
                else:
                    schemas = pt_schemas

                results[arm] = await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                )

            trial = ThreeArmTrial(
                task_name=task.name,
                ungated=results["ungated"],
                fixed_filter=results["fixed"],
                per_task_filter=results["per_task"],
            )
            all_trials.append(trial)

            if results_path is not None:
                data = {"trials": [_three_arm_trial_to_dict(t) for t in all_trials]}
                tmp = results_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2))
                os.replace(tmp, results_path)

    return AblationReport(trials=tuple(all_trials))


async def run_layered_benchmark(
    *,
    repetitions: int = 3,
    model: str = "claude-sonnet-4-6",
    results_path: Path | None = None,
) -> LaneReport:
    """Run the layered benchmark: deterministic bypass + ambiguous LLM path.

    1. Builds combined task set (12 deterministic + 6 ambiguous + 3 no-match)
    2. For each task, runs per-task filtering and classifies confidence
    3. DETERMINISTIC: bypass LLM, invoke executor directly
    4. AMBIGUOUS/NO_MATCH: send filtered tools to LLM
    5. Returns three-lane report
    """
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.gating import RuntimeEnvironment, gate_tools
    from tcp.harness.models import ToolSelectionRequest
    from tcp.harness.normalize import normalize_capability_descriptor
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    # Build tasks
    det_tasks = build_agent_tasks()
    amb_tasks_raw = build_ambiguous_tasks()
    amb_tasks = [
        AgentTask(
            name=at.agent_task.name,
            prompt=at.agent_task.prompt,
            expected_tool=at.agent_task.expected_tool,
            selection_request=at.selection_request,
        )
        for at in amb_tasks_raw
    ]
    all_tasks = det_tasks + amb_tasks

    # Build corpus (include synthetic tools from ambiguous tasks)
    entries = build_mcp_corpus()
    corpus_schemas = corpus_to_anthropic_schemas(entries)
    records = [normalize_capability_descriptor(e.descriptor) for e in entries]

    # Add synthetic tool records and schemas
    for at in amb_tasks_raw:
        for tool in at.synthetic_tools:
            records.append(tool)
            description = tool.rich_metadata.get(
                "description", tool.tool_name,
            )
            corpus_schemas.append({
                "name": tool.tool_name,
                "description": description,
                "input_schema": {"type": "object", "properties": {}},
            })

    schema_by_name = {s["name"]: s for s in corpus_schemas}
    all_names = frozenset(r.tool_name for r in records)
    env = RuntimeEnvironment(
        network_enabled=False,
        file_access_enabled=True,
        stdin_enabled=True,
        installed_tools=all_names,
    )
    default_request = ToolSelectionRequest.from_kwargs(
        preferred_criteria="speed",
        require_auto_approval=False,
    )

    # Map ambiguous task names to their synthetic tool records
    amb_synthetic: dict[str, list[ToolRecord]] = {}
    for at in amb_tasks_raw:
        amb_synthetic[at.agent_task.name] = list(at.synthetic_tools)

    mock_exec = get_mock_executor()
    all_metrics: list[LoopMetrics] = []

    for task in all_tasks:
        request = task.selection_request or default_request

        # Ambiguous tasks gate against their own synthetic tools only;
        # deterministic tasks gate against the full corpus.
        if task.name in amb_synthetic:
            gate_records = amb_synthetic[task.name]
        else:
            gate_records = records
        gate_result = gate_tools(gate_records, request, env)
        survivor_names = {t.tool_name for t in gate_result.approved_tools}
        survivor_names |= {t.tool_name for t in gate_result.approval_required_tools}
        filtered_schemas = [schema_by_name[n] for n in survivor_names if n in schema_by_name]

        survivor_count = len(survivor_names)
        if survivor_count == 0:
            confidence = RouteConfidence.NO_MATCH
        elif survivor_count == 1:
            confidence = RouteConfidence.DETERMINISTIC
        else:
            confidence = RouteConfidence.AMBIGUOUS

        route_result = RouteResult(
            selected_tool=None,
            confidence=confidence,
            survivor_count=survivor_count,
        )

        for _rep in range(repetitions):
            if should_bypass_llm(route_result):
                bypass_name = next(iter(survivor_names))
                metrics = await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=filtered_schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                    bypass_tool=bypass_name,
                )
            else:
                metrics = await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=filtered_schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                )

            # Patch routing metadata onto metrics
            metrics = replace(
                metrics,
                route_confidence=confidence.value,
                survivor_count=survivor_count,
            )
            all_metrics.append(metrics)

            if results_path is not None:
                tmp = results_path.with_suffix(".tmp")
                data = {"metrics": [_metrics_to_dict(m) for m in all_metrics]}
                tmp.write_text(json.dumps(data, indent=2))
                os.replace(tmp, results_path)

    return build_lane_report(all_metrics)
