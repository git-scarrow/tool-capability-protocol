"""Tests for EXP-2 agent task definitions."""

from __future__ import annotations

import pytest

from tcp.agent.tasks import AgentTask, build_agent_tasks


class TestAgentTask:
    """Verify AgentTask structure."""

    def test_frozen(self):
        task = AgentTask(
            name="test",
            prompt="Do something",
            expected_tool="some-tool",
        )
        with pytest.raises(AttributeError):
            task.name = "changed"  # type: ignore[misc]


class TestBuildAgentTasks:
    """Verify the 12 MT-3 agent tasks."""

    def test_returns_12_tasks(self):
        tasks = build_agent_tasks()
        assert len(tasks) == 12

    def test_all_have_prompts(self):
        tasks = build_agent_tasks()
        for t in tasks:
            assert isinstance(t.prompt, str)
            assert len(t.prompt) > 10, f"Task {t.name!r} has too short a prompt"

    def test_all_have_names(self):
        tasks = build_agent_tasks()
        names = [t.name for t in tasks]
        assert len(set(names)) == 12, "Task names must be unique"

    def test_expected_tools_match_mt3(self):
        """Expected tools align with MT-3 benchmark task expectations."""
        tasks = build_agent_tasks()
        by_name = {t.name: t for t in tasks}

        assert by_name["local file read"].expected_tool == "fs-read-file"
        assert by_name["local json processing"].expected_tool == "jq"
        assert by_name["git status check"].expected_tool == "git-status"
        assert by_name["file search"].expected_tool == "fs-search-files"
        assert by_name["semantic document search"].expected_tool == "rag-query-documents"
        assert by_name["git commit (write)"].expected_tool == "git-commit"
        assert by_name["approval-guarded privileged command"].expected_tool == "chmod"
        assert by_name["approval-guarded systemctl"].expected_tool == "systemctl"
        assert by_name["network fetch denied offline"].expected_tool is None
        assert by_name["database query denied offline"].expected_tool is None
        assert by_name["nonexistent command"].expected_tool is None
        assert by_name["require JSON output"].expected_tool == "jq"
