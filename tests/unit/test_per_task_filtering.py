"""Regression tests for per-task cold-path filtering (TCP-IMP-4).

Validates that per-task filtering is the default, produces narrower
tool sets than fixed filtering, and never excludes the expected tool.
"""

from __future__ import annotations

import pytest

from tcp.agent.benchmark import (
    build_filtered_schemas,
    build_fixed_filtered_schemas,
)
from tcp.agent.tasks import AgentTask, build_agent_tasks
from tcp.harness.corpus import build_mcp_corpus
from tcp.harness.schema_bridge import corpus_to_anthropic_schemas


@pytest.fixture(scope="module")
def corpus_schemas():
    entries = build_mcp_corpus()
    return corpus_to_anthropic_schemas(entries)


@pytest.fixture(scope="module")
def tasks():
    return build_agent_tasks()


class TestPerTaskIsDefault:
    """Per-task filtering is the default mode."""

    def test_default_mode_is_per_task(self, tasks, corpus_schemas):
        """build_filtered_schemas defaults to per-task, not fixed."""
        per_task = build_filtered_schemas(tasks, corpus_schemas)
        fixed = build_fixed_filtered_schemas(tasks, corpus_schemas)

        # Per-task should differ from fixed for at least some tasks
        any_differ = False
        for task in tasks:
            if len(per_task[task.name]) != len(fixed[task.name]):
                any_differ = True
                break
        assert any_differ, "Per-task and fixed produced identical sets for all tasks"

    def test_explicit_fixed_mode_matches_old_behavior(self, tasks, corpus_schemas):
        """mode='fixed' produces the same result as build_fixed_filtered_schemas."""
        fixed_direct = build_fixed_filtered_schemas(tasks, corpus_schemas)
        fixed_via_mode = build_filtered_schemas(tasks, corpus_schemas, mode="fixed")

        for task in tasks:
            direct_names = {s["name"] for s in fixed_direct[task.name]}
            mode_names = {s["name"] for s in fixed_via_mode[task.name]}
            assert direct_names == mode_names, f"Mismatch for {task.name}"


class TestPerTaskNarrower:
    """Per-task filtering produces narrower sets than fixed."""

    def test_per_task_narrower_on_tasks_with_requests(self, tasks, corpus_schemas):
        """Tasks with selection_request get fewer tools via per-task."""
        per_task = build_filtered_schemas(tasks, corpus_schemas)
        fixed = build_fixed_filtered_schemas(tasks, corpus_schemas)

        narrower_count = 0
        for task in tasks:
            if task.selection_request is not None:
                pt_count = len(per_task[task.name])
                fx_count = len(fixed[task.name])
                if pt_count < fx_count:
                    narrower_count += 1

        assert narrower_count > 0, "Per-task was never narrower than fixed"


class TestExpectedToolPreserved:
    """Per-task filtering never excludes the expected tool."""

    def test_expected_tool_in_per_task_set(self, tasks, corpus_schemas):
        """For tasks where expected_tool is not None and the tool should
        survive gating, verify it appears in the filtered set."""
        per_task = build_filtered_schemas(tasks, corpus_schemas)

        # Tasks where the expected tool should be in the filtered set
        # (excludes network-denied and nonexistent tasks)
        verifiable = [
            t for t in tasks
            if t.expected_tool is not None
            and t.name not in {
                "network fetch denied offline",
                "database query denied offline",
                "nonexistent command",
            }
        ]

        for task in verifiable:
            filtered_names = {s["name"] for s in per_task[task.name]}
            assert task.expected_tool in filtered_names, (
                f"Task {task.name!r}: expected tool {task.expected_tool!r} "
                f"not in per-task filtered set (got {len(filtered_names)} tools: "
                f"{sorted(filtered_names)[:5]}...)"
            )

    def test_denied_tasks_have_empty_or_no_target(self, tasks, corpus_schemas):
        """Network-denied tasks produce empty sets or exclude network tools."""
        per_task = build_filtered_schemas(tasks, corpus_schemas)

        denied_tasks = [
            t for t in tasks
            if t.name in {"network fetch denied offline", "database query denied offline"}
        ]
        for task in denied_tasks:
            filtered_names = {s["name"] for s in per_task[task.name]}
            # The expected tool is None, and network tools should be absent
            assert "web-fetch" not in filtered_names
            assert "oracle-execute-query" not in filtered_names


class TestAllTasksHaveSelectionRequest:
    """All 12 standard tasks now carry a selection_request."""

    def test_all_have_request(self, tasks):
        missing = [t.name for t in tasks if t.selection_request is None]
        assert not missing, f"Tasks without selection_request: {missing}"
