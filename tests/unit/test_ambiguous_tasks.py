"""Tests for the ambiguous task corpus."""

from __future__ import annotations

import pytest

from tcp.agent.ambiguous_tasks import build_ambiguous_tasks, AmbiguousTask


class TestAmbiguousTaskStructure:
    """Each ambiguous task has required fields."""

    def test_returns_list(self):
        tasks = build_ambiguous_tasks()
        assert isinstance(tasks, list)
        assert len(tasks) >= 6

    def test_all_have_required_fields(self):
        for task in build_ambiguous_tasks():
            assert isinstance(task, AmbiguousTask)
            assert task.agent_task.name
            assert task.agent_task.prompt
            assert task.agent_task.expected_tool is not None
            assert task.selection_request is not None
            assert task.ambiguity_reason

    def test_selection_requests_have_no_required_commands(self):
        """Ambiguous tasks use capability flags/formats, NOT specific commands."""
        for task in build_ambiguous_tasks():
            assert len(task.selection_request.required_commands) == 0, (
                f"Task {task.agent_task.name!r} has required_commands — "
                f"ambiguous tasks must use broader filters"
            )

    def test_synthetic_tools_provided(self):
        """Each task provides its synthetic tool records."""
        for task in build_ambiguous_tasks():
            assert len(task.synthetic_tools) >= 2, (
                f"Task {task.agent_task.name!r} needs 2+ synthetic tools, "
                f"got {len(task.synthetic_tools)}"
            )

    def test_expected_tool_in_synthetic_tools(self):
        """The expected tool appears in the synthetic tool set."""
        for task in build_ambiguous_tasks():
            tool_names = {t.tool_name for t in task.synthetic_tools}
            assert task.agent_task.expected_tool in tool_names, (
                f"Task {task.agent_task.name!r}: expected tool "
                f"{task.agent_task.expected_tool!r} not in synthetic tools "
                f"{sorted(tool_names)}"
            )
