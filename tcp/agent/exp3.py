"""EXP-3: 4-arm behavioral description ablation.

Question: Does behavioral prose in Claude Code tool descriptions still
add value after structural pre-filtering to 5-30 tools?

Arms:
  A — Realistic Ungated   : all 119 tools, full Claude Code descriptions
  B — Realistic Filtered  : all built-ins (full descriptions) + MCP survivors (corpus desc)
  C — Minimal Filtered    : all built-ins (minimal) + MCP survivors (minimal Commands/IO)
  D — Brief Filtered      : all built-ins (first paragraph only) + MCP survivors (first para)

Built-in tools always survive — they are always available in Claude Code regardless of
context. Only the MCP corpus is gated per-task. Arms B/C/D differ only in how richly
they describe the tools that survive.

Key comparisons:
  A vs B — does filtering hurt when descriptions are rich?
  B vs C — do richer descriptions help post-filtering?
  B vs D — do IMPORTANT/AVOID blocks add value beyond the core description?
  A vs C — production baseline (realistic full) vs TCP minimal approach.
"""

from __future__ import annotations

import json
import math
import os
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from tcp.agent.loop import LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.agent.tasks import AgentTask
from tcp.core.descriptors import CapabilityFlags


# ---------------------------------------------------------------------------
# Task set
# ---------------------------------------------------------------------------

def build_exp3_tasks() -> list[AgentTask]:
    """12 EXP-3 tasks: 6 targeting Claude Code built-ins + 6 MCP tasks.

    Built-in tasks are intentionally ambiguous between a built-in tool and a
    plausible MCP alternative so description quality drives the selection.
    """
    from tcp.harness.models import ToolSelectionRequest

    _files = int(CapabilityFlags.SUPPORTS_FILES)
    _json_out = int(CapabilityFlags.JSON_OUTPUT)

    return [
        # --- Built-in tasks ---
        AgentTask(
            name="builtin read file",
            prompt=(
                "Open the file at /tmp/config.yaml and display its contents. "
                "Show me every line."
            ),
            expected_tool="Read",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="builtin grep search",
            prompt=(
                "Search for all lines containing the pattern 'def test_' "
                "in Python files under the src/ directory. Include line numbers."
            ),
            expected_tool="Grep",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="builtin glob find",
            prompt=(
                "Find all TypeScript source files matching '**/*.ts' "
                "under the current project directory."
            ),
            expected_tool="Glob",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="builtin bash run",
            prompt=(
                "Run 'git log --oneline -10' to show the last 10 commit "
                "messages for this repository."
            ),
            expected_tool="Bash",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="builtin write file",
            prompt=(
                "Create a new file at /tmp/summary.txt containing the single "
                "line: 'Build passed — 2026-04-07'."
            ),
            expected_tool="Write",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="builtin edit file",
            prompt=(
                "In the file /tmp/app.py, replace every occurrence of "
                "'DEBUG = True' with 'DEBUG = False'."
            ),
            expected_tool="Edit",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- MCP tasks (from EXP-2 corpus) ---
        AgentTask(
            name="mcp read file",
            prompt="Read the file at /tmp/data.json and show me its contents.",
            expected_tool="fs-read-file",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"read_file"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="mcp json transform",
            prompt=(
                "Use jq to extract the 'name' field from the JSON file "
                "at /tmp/data.json."
            ),
            expected_tool="jq",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"jq"},
                required_input_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="mcp git status",
            prompt="Show me the current git status of the repository.",
            expected_tool="git-status",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_status"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="mcp file search",
            prompt=(
                "Use the filesystem search tool to find all files whose "
                "names match '*.config.json' under the /workspace directory."
            ),
            expected_tool="fs-search-files",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"search_files"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="mcp git commit",
            prompt=(
                "Commit the currently staged changes with the message "
                "'fix: resolve auth bug'."
            ),
            expected_tool="git-commit",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_commit"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="mcp json output",
            prompt=(
                "Use jq to extract the 'users' array from the JSON file "
                "at /tmp/response.json and pretty-print it."
            ),
            expected_tool="jq",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_json_out,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Description truncation helpers
# ---------------------------------------------------------------------------

def _brief_description(full: str) -> str:
    """Return first paragraph of a tool description (before first blank line).

    This strips IMPORTANT, AVOID, and extended usage notes — keeping only
    the core one-liner that says what the tool does.
    """
    # Split at the first double newline (paragraph boundary)
    first_para = full.split("\n\n")[0]
    # Also strip any leading whitespace on each line
    return first_para.strip()


def _minimal_description(name: str) -> str:
    """Return a bare-minimum one-line description for a built-in tool."""
    return name


# ---------------------------------------------------------------------------
# Schema arm builders
# ---------------------------------------------------------------------------

def build_arm_a_schemas() -> list[dict]:
    """Arm A: full realistic corpus, ungated (119 tools, rich descriptions)."""
    from tcp.harness.realistic_schemas import build_realistic_corpus
    return build_realistic_corpus()


def _build_mcp_survivors(
    task: AgentTask,
    records: list,
    env,
    default_request,
    schema_by_name: dict[str, dict],
    mode: str,
) -> list[dict]:
    """Gate MCP corpus for a task and return schemas in the requested mode.

    mode:
      "rich"    — use corpus_to_anthropic_schemas descriptions (d.description)
      "minimal" — use Commands/Input/Output from tool_record_to_anthropic_schema
      "brief"   — use first-paragraph of corpus description
    """
    from tcp.harness.gating import gate_tools
    from tcp.harness.schema_bridge import tool_record_to_anthropic_schema

    request = task.selection_request or default_request
    gate_result = gate_tools(records, request, env)
    survivor_names = {t.tool_name for t in gate_result.approved_tools}
    survivor_names |= {t.tool_name for t in gate_result.approval_required_tools}

    result: list[dict] = []
    for name in survivor_names:
        if name not in schema_by_name:
            continue
        if mode == "minimal":
            # Find the ToolRecord for this name
            record_map = {r.tool_name: r for r in records}
            if name in record_map:
                result.append(tool_record_to_anthropic_schema(record_map[name]))
        elif mode == "brief":
            s = schema_by_name[name]
            result.append({
                "name": s["name"],
                "description": _brief_description(s["description"]),
                "input_schema": s["input_schema"],
            })
        else:  # "rich" — use corpus description as-is
            result.append(schema_by_name[name])
    return result


def build_per_task_arm_schemas(
    tasks: list[AgentTask],
    *,
    network: bool = False,
) -> dict[str, dict[str, list[dict]]]:
    """Build per-task schemas for Arms B, C, and D.

    Returns: {task_name: {"B": [...], "C": [...], "D": [...]}}

    Arms B/C/D all include ALL Claude Code built-ins (they are always available
    in production) plus the MCP corpus survivors for each task. The arms differ
    only in description richness:

      B — built-ins: full realistic descriptions; MCP survivors: corpus description
      C — built-ins: tool-name only (minimal); MCP survivors: Commands/Input/Output
      D — built-ins: first paragraph only; MCP survivors: first paragraph only
    """
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.gating import RuntimeEnvironment
    from tcp.harness.models import ToolSelectionRequest
    from tcp.harness.normalize import normalize_capability_descriptor
    from tcp.harness.realistic_schemas import build_builtin_schemas
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    # MCP corpus
    entries = build_mcp_corpus()
    records = [normalize_capability_descriptor(e.descriptor) for e in entries]
    corpus_schemas = corpus_to_anthropic_schemas(entries)  # d.description level
    schema_by_name = {s["name"]: s for s in corpus_schemas}

    all_names = frozenset(r.tool_name for r in records)
    env = RuntimeEnvironment(
        network_enabled=network,
        file_access_enabled=True,
        stdin_enabled=True,
        installed_tools=all_names,
    )
    default_request = ToolSelectionRequest.from_kwargs(
        preferred_criteria="speed",
        require_auto_approval=False,
    )

    # Built-in schema lists (fixed, same for every task within each arm)
    builtin_full = build_builtin_schemas()  # rich Claude Code prose

    builtin_minimal = [
        {
            "name": s["name"],
            "description": _minimal_description(s["name"]),
            "input_schema": s["input_schema"],
        }
        for s in builtin_full
    ]

    builtin_brief = [
        {
            "name": s["name"],
            "description": _brief_description(s["description"]),
            "input_schema": s["input_schema"],
        }
        for s in builtin_full
    ]

    result: dict[str, dict[str, list[dict]]] = {}
    for task in tasks:
        mcp_rich = _build_mcp_survivors(
            task, records, env, default_request, schema_by_name, mode="rich"
        )
        mcp_minimal = _build_mcp_survivors(
            task, records, env, default_request, schema_by_name, mode="minimal"
        )
        mcp_brief = _build_mcp_survivors(
            task, records, env, default_request, schema_by_name, mode="brief"
        )

        result[task.name] = {
            "B": builtin_full + mcp_rich,
            "C": builtin_minimal + mcp_minimal,
            "D": builtin_brief + mcp_brief,
        }

    return result


# ---------------------------------------------------------------------------
# Results data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FourArmTrial:
    """One trial of the 4-arm ablation for a single task."""

    task_name: str
    arm_a: LoopMetrics
    arm_b: LoopMetrics
    arm_c: LoopMetrics
    arm_d: LoopMetrics


@dataclass(frozen=True)
class Exp3Report:
    """Full EXP-3 results with per-arm aggregate statistics."""

    trials: tuple[FourArmTrial, ...]

    def summary_table(self) -> str:
        """Human-readable per-arm summary across all tasks."""
        from collections import defaultdict

        by_task: dict[str, list[FourArmTrial]] = defaultdict(list)
        for t in self.trials:
            by_task[t.task_name].append(t)

        header = (
            f"{'Task':<32} {'A-Corr':>7} {'B-Corr':>7} {'C-Corr':>7} {'D-Corr':>7} "
            f"{'A-Tok':>7} {'B-Tok':>7} {'C-Tok':>7} {'D-Tok':>7}"
        )
        lines = [header, "-" * len(header)]

        totals = {k: [] for k in ["a_c", "b_c", "c_c", "d_c"]}
        for name, trials in sorted(by_task.items()):
            n = len(trials)
            a_c = sum(1 for t in trials if t.arm_a.selected_tool_correct) / n
            b_c = sum(1 for t in trials if t.arm_b.selected_tool_correct) / n
            c_c = sum(1 for t in trials if t.arm_c.selected_tool_correct) / n
            d_c = sum(1 for t in trials if t.arm_d.selected_tool_correct) / n
            a_tok = sum(t.arm_a.input_tokens for t in trials) // n
            b_tok = sum(t.arm_b.input_tokens for t in trials) // n
            c_tok = sum(t.arm_c.input_tokens for t in trials) // n
            d_tok = sum(t.arm_d.input_tokens for t in trials) // n
            lines.append(
                f"{name:<32} {a_c:>6.0%} {b_c:>6.0%} {c_c:>6.0%} {d_c:>6.0%} "
                f"{a_tok:>7} {b_tok:>7} {c_tok:>7} {d_tok:>7}"
            )
            totals["a_c"].append(a_c)
            totals["b_c"].append(b_c)
            totals["c_c"].append(c_c)
            totals["d_c"].append(d_c)

        n_tasks = len(by_task)
        if n_tasks:
            lines.append("-" * len(header))
            lines.append(
                f"{'MEAN':<32} "
                f"{sum(totals['a_c'])/n_tasks:>6.0%} "
                f"{sum(totals['b_c'])/n_tasks:>6.0%} "
                f"{sum(totals['c_c'])/n_tasks:>6.0%} "
                f"{sum(totals['d_c'])/n_tasks:>6.0%}"
            )

        return "\n".join(lines)

    def arm_summary(self) -> dict[str, dict[str, float]]:
        """Per-arm aggregate metrics."""
        arms = {"A": "arm_a", "B": "arm_b", "C": "arm_c", "D": "arm_d"}
        out: dict[str, dict[str, float]] = {}
        for label, attr in arms.items():
            metrics_list = [getattr(t, attr) for t in self.trials]
            n = len(metrics_list)
            if n == 0:
                continue
            correct = sum(1 for m in metrics_list if m.selected_tool_correct)
            out[label] = {
                "correctness": correct / n,
                "mean_input_tokens": sum(m.input_tokens for m in metrics_list) / n,
                "mean_tool_count": sum(m.tool_count for m in metrics_list) / n,
                "error_rate": sum(1 for m in metrics_list if m.error) / n,
                "trial_count": n,
            }
        return out


# ---------------------------------------------------------------------------
# Runner
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
    }


def _trial_to_dict(t: FourArmTrial) -> dict:
    return {
        "task_name": t.task_name,
        "arm_a": _metrics_to_dict(t.arm_a),
        "arm_b": _metrics_to_dict(t.arm_b),
        "arm_c": _metrics_to_dict(t.arm_c),
        "arm_d": _metrics_to_dict(t.arm_d),
    }


def _save_incremental(trials: list[FourArmTrial], path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    data = {"trials": [_trial_to_dict(t) for t in trials]}
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


async def run_exp3_ablation(
    *,
    repetitions: int = 3,
    model: str = "claude-sonnet-4-6",
    results_path: Path | None = None,
    network: bool = False,
) -> Exp3Report:
    """Run the 4-arm EXP-3 behavioral description ablation.

    For each task, all four arms run in randomized order to control for
    API-level prompt caching and ordering effects.
    """
    tasks = build_exp3_tasks()
    arm_a_schemas = build_arm_a_schemas()
    per_task_schemas = build_per_task_arm_schemas(tasks, network=network)

    mock_exec = get_mock_executor()
    all_trials: list[FourArmTrial] = []

    for task in tasks:
        bcd = per_task_schemas[task.name]
        schemas_b = bcd["B"]
        schemas_c = bcd["C"]
        schemas_d = bcd["D"]

        print(
            f"  [{task.name}] "
            f"A={len(arm_a_schemas)} B={len(schemas_b)} "
            f"C={len(schemas_c)} D={len(schemas_d)} tools"
        )

        for _rep in range(repetitions):
            # Randomize arm order to control for prompt-cache effects
            arm_order = ["A", "B", "C", "D"]
            random.shuffle(arm_order)

            results: dict[str, LoopMetrics] = {}
            for arm in arm_order:
                if arm == "A":
                    schemas = arm_a_schemas
                elif arm == "B":
                    schemas = schemas_b
                elif arm == "C":
                    schemas = schemas_c
                else:
                    schemas = schemas_d

                results[arm] = await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                )

            trial = FourArmTrial(
                task_name=task.name,
                arm_a=results["A"],
                arm_b=results["B"],
                arm_c=results["C"],
                arm_d=results["D"],
            )
            all_trials.append(trial)

            if results_path is not None:
                _save_incremental(all_trials, results_path)

            # Quick per-rep progress line
            a_ok = "✓" if results["A"].selected_tool_correct else "✗"
            b_ok = "✓" if results["B"].selected_tool_correct else "✗"
            c_ok = "✓" if results["C"].selected_tool_correct else "✗"
            d_ok = "✓" if results["D"].selected_tool_correct else "✗"
            print(f"    rep {_rep+1}: A{a_ok} B{b_ok} C{c_ok} D{d_ok}")

    return Exp3Report(trials=tuple(all_trials))
