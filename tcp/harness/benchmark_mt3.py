"""MT-3 benchmark: real-tool corpus at 90-tool scale.

Reuses the three-path comparison infrastructure from benchmark.py but with
a 90-tool corpus derived from live MCP tool registries and system commands.
"""

from __future__ import annotations

from tcp.core.descriptors import CapabilityFlags
from tcp.harness.benchmark import (
    BenchmarkTask,
    benchmark_exposure_paths,
    benchmark_exposure_suite,
    summarize_comparisons,
)
from tcp.harness.corpus import build_mt3_corpus, corpus_summary, CorpusEntry
from tcp.harness.gating import RuntimeEnvironment
from tcp.harness.models import ToolSelectionRequest


def build_mt3_environment(
    *,
    network: bool = False,
    file_access: bool = True,
) -> RuntimeEnvironment:
    """Build environment with all corpus tools installed."""
    from tcp.harness.corpus import build_mcp_corpus
    entries = build_mcp_corpus()
    all_names = frozenset(e.descriptor.name for e in entries)
    return RuntimeEnvironment(
        network_enabled=network,
        file_access_enabled=file_access,
        stdin_enabled=True,
        installed_tools=all_names,
    )


def build_mt3_tasks() -> list[BenchmarkTask]:
    """Build diverse task fixtures for the 90-tool corpus."""
    return [
        # --- Offline local tasks (network denied) ---
        BenchmarkTask(
            name="local file read",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"read_file"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"fs-read-file"}),
            expected_approved_tool_names=frozenset({"fs-read-file"}),
        ),
        BenchmarkTask(
            name="local json processing",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"jq"},
                required_input_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"jq"}),
            expected_approved_tool_names=frozenset({"jq"}),
        ),
        BenchmarkTask(
            name="git status check",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_status"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"git-status"}),
            expected_approved_tool_names=frozenset({"git-status"}),
        ),
        BenchmarkTask(
            name="file search",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"search_files"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"fs-search-files"}),
            expected_approved_tool_names=frozenset({"fs-search-files"}),
        ),
        BenchmarkTask(
            name="semantic document search",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"query_documents"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"rag-query-documents"}),
            expected_approved_tool_names=frozenset({"rag-query-documents"}),
        ),
        BenchmarkTask(
            name="git commit (write)",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_commit"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"git-commit"}),
            expected_approved_tool_names=frozenset({"git-commit"}),
        ),
        # --- Tasks requiring approval gating ---
        BenchmarkTask(
            name="approval-guarded privileged command",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"chmod"},
                preferred_criteria="speed",
                require_auto_approval=True,
            ),
            expected_tool_names=frozenset(),  # no auto-approved tool
            expected_approved_tool_names=frozenset(),
            expected_approval_required_tool_names=frozenset({"chmod"}),
        ),
        BenchmarkTask(
            name="approval-guarded systemctl",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"systemctl"},
                preferred_criteria="speed",
                require_auto_approval=True,
            ),
            expected_tool_names=frozenset(),
            expected_approved_tool_names=frozenset(),
            expected_approval_required_tool_names=frozenset({"systemctl"}),
        ),
        # --- Network tasks (denied in offline env) ---
        BenchmarkTask(
            name="network fetch denied offline",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"fetch"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset(),
            expected_approved_tool_names=frozenset(),
            expected_rejected_tool_names=frozenset({"web-fetch"}),
        ),
        BenchmarkTask(
            name="database query denied offline",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"execute_query"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset(),
            expected_approved_tool_names=frozenset(),
            expected_rejected_tool_names=frozenset({"oracle-execute-query"}),
        ),
        # --- No-match tasks (rejection behavior) ---
        BenchmarkTask(
            name="nonexistent command",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"quantum_teleport"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset(),
            expected_approved_tool_names=frozenset(),
        ),
        # --- Capability-flag required tasks ---
        BenchmarkTask(
            name="require JSON output",
            request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=int(CapabilityFlags.JSON_OUTPUT),
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"jq"}),
            expected_approved_tool_names=frozenset({"jq"}),
        ),
    ]


def run_mt3_benchmark(*, repetitions: int = 5) -> dict:
    """Run the full MT-3 benchmark suite and return results."""
    descriptors, entries = build_mt3_corpus()
    summary_info = corpus_summary(entries)
    tasks = build_mt3_tasks()
    environment = build_mt3_environment(network=False, file_access=True)

    suite = benchmark_exposure_suite(
        descriptors, tasks, environment, repetitions=repetitions,
    )

    return {
        "corpus": summary_info,
        "task_count": len(tasks),
        "repetitions": repetitions,
        "suite_summary": suite.summary,
        "comparisons": suite.comparisons,
    }
