"""Paired benchmark runner for EXP-2.

Runs filtered/unfiltered trials with randomized ordering to control for
prompt-cache state and network conditions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from tcp.agent.loop import LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.agent.tasks import AgentTask


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
) -> dict[str, list[dict]]:
    """Build per-task filtered schema subsets using TCP gating.

    Uses the MT-3 offline environment (network denied) with bitmask
    filtering.  Each task gets the bitmask survivors as its filtered set.
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

    # Offline environment: deny network tools
    deny = EnvironmentMask.from_constraints(network=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    result = bitmask_filter(records, deny_mask=deny, approval_mask=approval)
    survivor_names = frozenset(r.tool_name for r in result.survivors)

    # Every task gets the same bitmask-filtered set (offline environment)
    filtered_schemas = [
        schema_by_name[name] for name in survivor_names if name in schema_by_name
    ]

    return {task.name: filtered_schemas for task in tasks}


async def run_paired_benchmark(
    tasks: list[AgentTask],
    corpus_schemas: list[dict],
    filtered_schemas_by_task: dict[str, list[dict]],
    *,
    repetitions: int = 5,
    model: str = "claude-sonnet-4-6",
) -> BenchmarkReport:
    """Run paired filtered/unfiltered trials for each task.

    Order is randomized per pair to control for prompt caching.
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

            all_trials.append(
                PairedTrial(
                    task_name=task.name,
                    unfiltered=unfiltered_metrics,
                    filtered=filtered_metrics,
                )
            )

    return BenchmarkReport.from_trials(all_trials)
