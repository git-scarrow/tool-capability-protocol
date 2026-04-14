"""EXP-6: Primacy-bias experiment on the expanded adversarial ambiguous-lane corpus.

Question: Does tool selection correctness drop when the correct tool is NOT
listed first in the survivor set?

Two conditions (ordering of the same tool set per task):
  CORRECT_FIRST     — correct tool placed at index 0
  CORRECT_NOT_FIRST — correct tool placed at a non-first position
                      (half at middle, half at last)

All other variables held constant: prompt, model, description quality.

Metrics collected per trial:
  first_tool_correctness  — was the first tool called the expected tool?
  any_position_correct    — was the expected tool called at any point?
  input_tokens            — prompt token cost
  latency_ms              — total response time
  error_rate              — did the API call fail?
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from tcp.agent.ambiguous_tasks import AmbiguousTask, build_ambiguous_tasks
from tcp.agent.loop import LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.harness.models import ToolRecord


# ---------------------------------------------------------------------------
# Ordering helpers
# ---------------------------------------------------------------------------

def reorder_tools(
    tools: list[ToolRecord],
    expected_tool: str,
    position: str,
) -> list[ToolRecord]:
    """Return a reordered copy placing expected_tool at the given position.

    position: "correct-first" | "correct-middle" | "correct-last"

    If expected_tool is not in tools, returns tools unchanged.
    """
    correct = [t for t in tools if t.tool_name == expected_tool]
    others = [t for t in tools if t.tool_name != expected_tool]

    if not correct:
        return list(tools)

    if position == "correct-first":
        return correct + others
    elif position == "correct-last":
        return others + correct
    else:  # correct-middle
        mid = max(1, len(others) // 2)
        return others[:mid] + correct + others[mid:]


def ambiguous_task_to_schemas(tools: list[ToolRecord]) -> list[dict]:
    """Convert synthetic ToolRecords to Anthropic tool schema dicts.

    Uses rich_metadata["description"] for the tool description, falling
    back to a Commands/Input/Output summary. Uses rich_metadata["input_schema"]
    if present, otherwise a generic single-string schema.
    """
    schemas: list[dict] = []
    for t in tools:
        meta = t.rich_metadata or {}
        description = meta.get("description", t.tool_name)
        input_schema = meta.get(
            "input_schema",
            {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": f"Input for {t.tool_name}",
                    },
                },
            },
        )
        schemas.append(
            {
                "name": t.tool_name,
                "description": description,
                "input_schema": input_schema,
            }
        )
    return schemas


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

POSITIONS = ("correct-first", "correct-middle", "correct-last")


@dataclass(frozen=True)
class PrimacyBiasTrial:
    """One task evaluated under all three orderings."""

    task_name: str
    expected_tool: str
    metrics_first: LoopMetrics   # correct tool at index 0
    metrics_middle: LoopMetrics  # correct tool at middle
    metrics_last: LoopMetrics    # correct tool at last


@dataclass
class Exp6Report:
    """Full EXP-6 results with primacy-bias analysis."""

    trials: list[PrimacyBiasTrial]
    model: str
    repetitions: int

    def _all_metrics(self, position: str) -> list[LoopMetrics]:
        attr = {
            "correct-first": "metrics_first",
            "correct-middle": "metrics_middle",
            "correct-last": "metrics_last",
        }[position]
        return [getattr(t, attr) for t in self.trials]

    def _correctness(self, metrics: list[LoopMetrics], metric: str) -> float:
        n = len(metrics)
        if n == 0:
            return 0.0
        if metric == "first_tool":
            return sum(1 for m in metrics if m.selected_tool_correct) / n
        else:  # any_position
            return sum(1 for m in metrics if m.expected_tool_any_position) / n

    def summary_table(self) -> str:
        """Human-readable correctness table across positions."""
        header = (
            f"{'Task':<32} "
            f"{'1st-F':>6} {'1st-M':>6} {'1st-L':>6} "
            f"{'Any-F':>6} {'Any-M':>6} {'Any-L':>6} "
            f"{'Tok-F':>7} {'Tok-M':>7} {'Tok-L':>7}"
        )
        sep = "-" * len(header)
        rows = [header, sep]

        for t in self.trials:
            mf, mm, ml = t.metrics_first, t.metrics_middle, t.metrics_last
            rows.append(
                f"{t.task_name:<32} "
                f"{int(mf.selected_tool_correct):>6} "
                f"{int(mm.selected_tool_correct):>6} "
                f"{int(ml.selected_tool_correct):>6} "
                f"{int(mf.expected_tool_any_position):>6} "
                f"{int(mm.expected_tool_any_position):>6} "
                f"{int(ml.expected_tool_any_position):>6} "
                f"{mf.input_tokens:>7} "
                f"{mm.input_tokens:>7} "
                f"{ml.input_tokens:>7}"
            )

        # Aggregates
        rows.append(sep)
        for pos, label in [("correct-first", "FIRST"), ("correct-middle", "MID"), ("correct-last", "LAST")]:
            ms = self._all_metrics(pos)
            n = len(ms)
            if n == 0:
                continue
            ftc = self._correctness(ms, "first_tool")
            apc = self._correctness(ms, "any_position")
            tok = sum(m.input_tokens for m in ms) / n
            err = sum(1 for m in ms if m.error) / n
            rows.append(
                f"{'MEAN ' + label:<32} "
                f"{ftc:>6.1%} {'':>6} {'':>6} "
                f"{apc:>6.1%} {'':>6} {'':>6} "
                f"{tok:>7.0f}"
            )
            rows.append(f"  error_rate={err:.1%}")

        return "\n".join(rows)

    def mt12_aggregate_metrics(self) -> dict:
        """Compute TCP-MT-12 required aggregate metrics across all trials.

        Returns a dict with:
          - ambiguous_lane_miss_rate: first-tool miss rate on ambiguous-lane turns
          - mean_retry_latency_penalty_ms: mean penalty across misses
          - miss_rate_by_similarity_quartile: miss rate per description_similarity_max quartile
          - miss_rate_pack_promotion: miss rate on pack-promotion turns (caveat: always 0 in harness)
          - miss_rate_schema_load_on_demand: miss rate on schema-load turns (caveat: always 0)
          - latency_attribution: mean latency breakdown for miss vs hit turns
        """
        all_metrics: list[LoopMetrics] = []
        for t in self.trials:
            all_metrics.extend([t.metrics_first, t.metrics_middle, t.metrics_last])

        # Filter to error-free turns only
        valid = [m for m in all_metrics if m.error is None]
        ambiguous = [m for m in valid if m.ambiguous_lane]

        n_ambiguous = len(ambiguous)
        n_miss = sum(1 for m in ambiguous if not m.selected_tool_correct)
        ambiguous_lane_miss_rate = n_miss / n_ambiguous if n_ambiguous > 0 else 0.0

        misses = [m for m in ambiguous if not m.selected_tool_correct]
        mean_retry_latency_ms = (
            sum(m.retry_latency_penalty_ms for m in misses) / len(misses)
            if misses else 0.0
        )

        # Miss rate by description_similarity_max quartile
        similarities = sorted(m.description_similarity_max for m in ambiguous)
        n = len(similarities)
        q_bounds = [0.0, 0.0, 0.0, 0.0]
        if n >= 4:
            q_bounds = [
                similarities[n // 4],
                similarities[n // 2],
                similarities[3 * n // 4],
                similarities[-1] + 0.001,
            ]

        def _quartile(sim: float) -> int:
            for i, bound in enumerate(q_bounds):
                if sim <= bound:
                    return i
            return 3

        miss_by_q: dict[int, list[bool]] = {0: [], 1: [], 2: [], 3: []}
        for m in ambiguous:
            q = _quartile(m.description_similarity_max)
            miss_by_q[q].append(not m.selected_tool_correct)

        miss_rate_by_quartile = {
            f"q{i+1}": (
                sum(v) / len(v) if v else None
            )
            for i, v in miss_by_q.items()
        }

        # Pack promotion / schema load (always False in harness — caveat documented)
        promotion_turns = [m for m in ambiguous if m.pack_promotion_triggered]
        schema_turns = [m for m in ambiguous if m.schema_load_on_demand]
        miss_rate_pack_promotion = (
            sum(1 for m in promotion_turns if not m.selected_tool_correct) / len(promotion_turns)
            if promotion_turns else None
        )
        miss_rate_schema_load = (
            sum(1 for m in schema_turns if not m.selected_tool_correct) / len(schema_turns)
            if schema_turns else None
        )

        # Latency attribution: mean total_response_time for hits vs misses
        hits = [m for m in ambiguous if m.selected_tool_correct]
        latency_attribution = {
            "mean_total_ms_on_hit": (
                sum(m.total_response_time_ms for m in hits) / len(hits) if hits else 0.0
            ),
            "mean_total_ms_on_miss": (
                sum(m.total_response_time_ms for m in misses) / len(misses) if misses else 0.0
            ),
            "mean_retry_penalty_ms_on_miss": mean_retry_latency_ms,
        }

        return {
            "n_ambiguous_turns": n_ambiguous,
            "n_miss": n_miss,
            "ambiguous_lane_miss_rate": ambiguous_lane_miss_rate,
            "mean_retry_latency_penalty_ms": mean_retry_latency_ms,
            "miss_rate_by_similarity_quartile": miss_rate_by_quartile,
            "miss_rate_pack_promotion": miss_rate_pack_promotion,
            "miss_rate_schema_load_on_demand": miss_rate_schema_load,
            "latency_attribution": latency_attribution,
            "caveats": {
                "pack_promotion_triggered_always_false": all(
                    not m.pack_promotion_triggered for m in ambiguous
                ),
                "schema_load_on_demand_always_false": all(
                    not m.schema_load_on_demand for m in ambiguous
                ),
                "note": (
                    "pack_promotion_triggered and schema_load_on_demand are "
                    "always False in the synthetic harness. Deferred-schema "
                    "latency cost requires proxy-intercepted live turns."
                ),
            },
        }

    def primacy_bias_summary(self) -> dict:
        """Key metrics for the primacy-bias comparison.

        Primary comparison: correct-first vs correct-not-first (middle + last).
        """
        first_ms = self._all_metrics("correct-first")
        mid_ms = self._all_metrics("correct-middle")
        last_ms = self._all_metrics("correct-last")
        not_first_ms = mid_ms + last_ms

        return {
            "n_tasks": len(self.trials),
            "n_total_trials": len(first_ms) + len(mid_ms) + len(last_ms),
            "model": self.model,
            "repetitions": self.repetitions,
            "correct_first": {
                "first_tool_correctness": self._correctness(first_ms, "first_tool"),
                "any_position_correctness": self._correctness(first_ms, "any_position"),
                "mean_input_tokens": sum(m.input_tokens for m in first_ms) / max(1, len(first_ms)),
                "mean_latency_ms": sum(m.total_response_time_ms for m in first_ms) / max(1, len(first_ms)),
                "error_rate": sum(1 for m in first_ms if m.error) / max(1, len(first_ms)),
            },
            "correct_not_first": {
                "first_tool_correctness": self._correctness(not_first_ms, "first_tool"),
                "any_position_correctness": self._correctness(not_first_ms, "any_position"),
                "mean_input_tokens": sum(m.input_tokens for m in not_first_ms) / max(1, len(not_first_ms)),
                "mean_latency_ms": sum(m.total_response_time_ms for m in not_first_ms) / max(1, len(not_first_ms)),
                "error_rate": sum(1 for m in not_first_ms if m.error) / max(1, len(not_first_ms)),
            },
            "delta_first_tool_correctness": (
                self._correctness(first_ms, "first_tool")
                - self._correctness(not_first_ms, "first_tool")
            ),
            "delta_any_position_correctness": (
                self._correctness(first_ms, "any_position")
                - self._correctness(not_first_ms, "any_position")
            ),
            "stop_condition_met": (
                abs(
                    self._correctness(first_ms, "any_position")
                    - self._correctness(not_first_ms, "any_position")
                ) < 0.01
            ),
        }


# ---------------------------------------------------------------------------
# TCP-MT-12 reopen gate
# ---------------------------------------------------------------------------

REOPEN_GATE_MISS_RATE_THRESHOLD = 0.15   # 15% ambiguous-lane first-tool miss rate
REOPEN_GATE_LATENCY_THRESHOLD_MS = 500.0  # 500ms mean retry latency penalty


def check_reopen_gate(report: "Exp6Report") -> dict:
    """Evaluate the TCP-MT-12 automated reopen gate.

    Returns a dict with:
      - gate_fired: bool — True if both conditions are met
      - miss_rate: float — measured ambiguous-lane first-tool miss rate
      - mean_retry_latency_ms: float — measured mean retry latency penalty
      - conditions: dict — individual condition pass/fail
      - recommendation: str — human-readable recommendation

    Gate fires when BOTH:
      1. ambiguous_lane_miss_rate > 15%
      2. mean_retry_latency_penalty_ms > 500ms

    When fired, the successor item should be scoped to mitigating first-tool
    miss latency under the deferred-schema model.
    """
    agg = report.mt12_aggregate_metrics()
    miss_rate = agg["ambiguous_lane_miss_rate"]
    mean_retry_ms = agg["mean_retry_latency_penalty_ms"]

    cond_miss = miss_rate > REOPEN_GATE_MISS_RATE_THRESHOLD
    cond_latency = mean_retry_ms > REOPEN_GATE_LATENCY_THRESHOLD_MS
    gate_fired = cond_miss and cond_latency

    if gate_fired:
        recommendation = (
            f"Gate FIRED — ambiguous-lane miss rate {miss_rate:.1%} > "
            f"{REOPEN_GATE_MISS_RATE_THRESHOLD:.0%} AND mean retry penalty "
            f"{mean_retry_ms:.0f}ms > {REOPEN_GATE_LATENCY_THRESHOLD_MS:.0f}ms. "
            "Open successor WI scoped to mitigating first-tool miss latency "
            "under the deferred-schema model."
        )
    else:
        reasons = []
        if not cond_miss:
            reasons.append(
                f"miss rate {miss_rate:.1%} ≤ {REOPEN_GATE_MISS_RATE_THRESHOLD:.0%}"
            )
        if not cond_latency:
            reasons.append(
                f"retry penalty {mean_retry_ms:.0f}ms ≤ {REOPEN_GATE_LATENCY_THRESHOLD_MS:.0f}ms"
            )
        recommendation = f"Gate did not fire ({'; '.join(reasons)}). No successor needed."

    return {
        "gate_fired": gate_fired,
        "miss_rate": miss_rate,
        "mean_retry_latency_ms": mean_retry_ms,
        "conditions": {
            "miss_rate_exceeded": cond_miss,
            "latency_exceeded": cond_latency,
            "thresholds": {
                "miss_rate": REOPEN_GATE_MISS_RATE_THRESHOLD,
                "latency_ms": REOPEN_GATE_LATENCY_THRESHOLD_MS,
            },
        },
        "recommendation": recommendation,
        "n_ambiguous_turns": agg["n_ambiguous_turns"],
        "n_miss": agg["n_miss"],
    }


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _metrics_to_dict(m: LoopMetrics) -> dict:
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
        # TCP-MT-12 telemetry fields
        "first_tool_name": m.first_tool_name,
        "expected_tool_name": m.expected_tool_name,
        "retry_latency_penalty_ms": m.retry_latency_penalty_ms,
        "description_similarity_max": m.description_similarity_max,
        "ambiguous_lane": m.ambiguous_lane,
        "pack_promotion_triggered": m.pack_promotion_triggered,
        "schema_load_on_demand": m.schema_load_on_demand,
    }


def _trial_to_dict(t: PrimacyBiasTrial) -> dict:
    return {
        "task_name": t.task_name,
        "expected_tool": t.expected_tool,
        "correct_first": _metrics_to_dict(t.metrics_first),
        "correct_middle": _metrics_to_dict(t.metrics_middle),
        "correct_last": _metrics_to_dict(t.metrics_last),
    }


def _save_incremental(
    trials: list[PrimacyBiasTrial],
    path: Path,
    model: str,
    repetitions: int,
) -> None:
    tmp = path.with_suffix(".tmp")
    data = {
        "model": model,
        "repetitions": repetitions,
        "trials": [_trial_to_dict(t) for t in trials],
    }
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_exp6(
    *,
    model: str = "claude-sonnet-4-6",
    repetitions: int = 1,
    results_path: Path | None = None,
    task_filter: set[str] | None = None,
) -> Exp6Report:
    """Run the EXP-6 primacy-bias experiment.

    For each ambiguous task, runs three orderings (correct-first, correct-middle,
    correct-last) for `repetitions` rounds each. All other variables are held
    constant (same prompt, model, and tool descriptions).

    Args:
        model: Anthropic model identifier.
        repetitions: Number of rounds per (task, position) combination.
        results_path: If set, saves incremental JSON results to this path.
        task_filter: If set, only run tasks whose name is in this set.

    Returns:
        Exp6Report with all trials and summary statistics.
    """
    tasks = build_ambiguous_tasks()
    if task_filter:
        tasks = [t for t in tasks if t.agent_task.name in task_filter]

    n_tasks = len(tasks)
    if n_tasks < 30 and task_filter is None:
        print(f"  WARNING: only {n_tasks} tasks — stop condition requires ≥30")

    mock_exec = get_mock_executor()
    all_trials: list[PrimacyBiasTrial] = []

    for task in tasks:
        expected = task.agent_task.expected_tool or ""
        tools_list = list(task.synthetic_tools)

        ordered_tools = {
            pos: reorder_tools(tools_list, expected, pos)
            for pos in POSITIONS
        }
        schema_sets = {
            pos: ambiguous_task_to_schemas(ordered_tools[pos])
            for pos in POSITIONS
        }

        print(
            f"  [{task.agent_task.name}] {len(tools_list)} tools | "
            f"expected={expected!r}"
        )

        for rep in range(repetitions):
            results: dict[str, LoopMetrics] = {}
            for pos in POSITIONS:
                results[pos] = await run_agent_loop(
                    task_prompt=task.agent_task.prompt,
                    tools=schema_sets[pos],
                    mock_executor=mock_exec,
                    expected_tool=expected,
                    task_name=task.agent_task.name,
                    model=model,
                )

            trial = PrimacyBiasTrial(
                task_name=task.agent_task.name,
                expected_tool=expected,
                metrics_first=results["correct-first"],
                metrics_middle=results["correct-middle"],
                metrics_last=results["correct-last"],
            )
            all_trials.append(trial)

            if results_path is not None:
                _save_incremental(all_trials, results_path, model, repetitions)

            # Progress line
            f_ok = "✓" if results["correct-first"].selected_tool_correct else "✗"
            m_ok = "✓" if results["correct-middle"].selected_tool_correct else "✗"
            l_ok = "✓" if results["correct-last"].selected_tool_correct else "✗"
            fa_ok = "✓" if results["correct-first"].expected_tool_any_position else "✗"
            ma_ok = "✓" if results["correct-middle"].expected_tool_any_position else "✗"
            la_ok = "✓" if results["correct-last"].expected_tool_any_position else "✗"
            print(
                f"    rep {rep+1}: "
                f"1st=[F{f_ok} M{m_ok} L{l_ok}]  "
                f"any=[F{fa_ok} M{ma_ok} L{la_ok}]"
            )

    return Exp6Report(trials=all_trials, model=model, repetitions=repetitions)
