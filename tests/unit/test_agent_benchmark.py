"""Tests for the paired benchmark runner.

All tests mock run_agent_loop -- no real API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tcp.agent.benchmark import (
    BenchmarkReport,
    PairedTrial,
    build_filtered_schemas,
    run_paired_benchmark,
)
from tcp.agent.loop import LoopMetrics
from tcp.agent.tasks import AgentTask


def _make_metrics(
    task_name: str = "test",
    tool_count: int = 90,
    turns: int = 2,
    first_token_latency_ms: float = 100.0,
    total_response_time_ms: float = 200.0,
    input_tokens: int = 500,
    output_tokens: int = 100,
    tools_called: tuple[str, ...] = ("fs-read-file",),
    selected_tool_correct: bool = True,
    error: str | None = None,
) -> LoopMetrics:
    return LoopMetrics(
        task_name=task_name,
        tool_count=tool_count,
        turns=turns,
        first_token_latency_ms=first_token_latency_ms,
        total_response_time_ms=total_response_time_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tools_called=tools_called,
        selected_tool_correct=selected_tool_correct,
        error=error,
    )


class TestPairedTrial:
    """PairedTrial delta properties."""

    def test_latency_delta(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(total_response_time_ms=300.0),
            filtered=_make_metrics(total_response_time_ms=200.0),
        )
        assert trial.latency_delta_ms == pytest.approx(100.0)

    def test_token_delta(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(input_tokens=1000),
            filtered=_make_metrics(input_tokens=400),
        )
        assert trial.token_delta == 600

    def test_frozen(self):
        trial = PairedTrial(
            task_name="t",
            unfiltered=_make_metrics(),
            filtered=_make_metrics(),
        )
        with pytest.raises(AttributeError):
            trial.task_name = "changed"  # type: ignore[misc]


class TestBuildFilteredSchemas:
    """Test filtered schema generation from corpus + gating."""

    def test_returns_dict_keyed_by_task_name(self):
        from tcp.agent.tasks import build_agent_tasks

        tasks = build_agent_tasks()
        corpus_schemas = [
            {
                "name": f"tool-{i}",
                "description": f"t{i}",
                "input_schema": {"type": "object", "properties": {}},
            }
            for i in range(10)
        ]
        filtered = build_filtered_schemas(tasks, corpus_schemas)
        assert isinstance(filtered, dict)
        assert set(filtered.keys()) == {t.name for t in tasks}

    def test_filtered_is_subset_of_corpus(self):
        from tcp.agent.tasks import build_agent_tasks

        tasks = build_agent_tasks()
        corpus_schemas = [
            {
                "name": f"tool-{i}",
                "description": f"t{i}",
                "input_schema": {"type": "object", "properties": {}},
            }
            for i in range(10)
        ]
        corpus_names = {s["name"] for s in corpus_schemas}
        filtered = build_filtered_schemas(tasks, corpus_schemas)
        for task_name, schemas in filtered.items():
            for s in schemas:
                assert s["name"] in corpus_names, (
                    f"Filtered schema {s['name']} for task {task_name!r} "
                    f"not in corpus"
                )

    def test_mt3_corpus_filtering(self):
        """With real MT-3 corpus, filtered sets are smaller than unfiltered."""
        from tcp.agent.tasks import build_agent_tasks
        from tcp.harness.corpus import build_mcp_corpus
        from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

        entries = build_mcp_corpus()
        corpus_schemas = corpus_to_anthropic_schemas(entries)
        tasks = build_agent_tasks()
        filtered = build_filtered_schemas(tasks, corpus_schemas)

        full_count = len(corpus_schemas)
        any_reduced = any(
            len(schemas) < full_count for schemas in filtered.values()
        )
        assert any_reduced, "No task had a reduced tool set after filtering"


class TestBenchmarkReport:
    """BenchmarkReport summary computation."""

    def test_summary_keys(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(
                input_tokens=1000,
                total_response_time_ms=300.0,
                selected_tool_correct=True,
            ),
            filtered=_make_metrics(
                input_tokens=400,
                total_response_time_ms=200.0,
                selected_tool_correct=True,
            ),
        )
        report = BenchmarkReport.from_trials([trial])
        assert "mean_latency_delta_ms" in report.summary
        assert "mean_token_delta" in report.summary
        assert "filtered_correct_rate" in report.summary
        assert "unfiltered_correct_rate" in report.summary
        assert "trial_count" in report.summary

    def test_summary_values(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(
                input_tokens=1000,
                total_response_time_ms=300.0,
                selected_tool_correct=True,
            ),
            filtered=_make_metrics(
                input_tokens=400,
                total_response_time_ms=200.0,
                selected_tool_correct=True,
            ),
        )
        report = BenchmarkReport.from_trials([trial])
        assert report.summary["mean_latency_delta_ms"] == pytest.approx(100.0)
        assert report.summary["mean_token_delta"] == 600
        assert report.summary["filtered_correct_rate"] == pytest.approx(1.0)
        assert report.summary["trial_count"] == 1

    def test_empty_trials(self):
        report = BenchmarkReport.from_trials([])
        assert report.summary["trial_count"] == 0


@pytest.mark.asyncio
class TestRunPairedBenchmark:
    """Test the full paired benchmark runner with mocked loop."""

    async def test_runs_correct_number_of_trials(self):
        tasks = [
            AgentTask(name="task-a", prompt="Do A", expected_tool="tool-a"),
            AgentTask(name="task-b", prompt="Do B", expected_tool="tool-b"),
        ]
        corpus_schemas = [
            {
                "name": "tool-a",
                "description": "A",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "tool-b",
                "description": "B",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
        filtered = {
            "task-a": [corpus_schemas[0]],
            "task-b": [corpus_schemas[1]],
        }

        call_count = 0

        async def mock_loop(task_prompt, tools, mock_executor, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_metrics(
                task_name=kwargs.get("task_name", "test"),
                tool_count=len(tools),
            )

        with patch("tcp.agent.benchmark.run_agent_loop", side_effect=mock_loop):
            report = await run_paired_benchmark(
                tasks=tasks,
                corpus_schemas=corpus_schemas,
                filtered_schemas_by_task=filtered,
                repetitions=3,
            )

        # 2 tasks x 3 reps x 2 arms = 12 loop calls
        assert call_count == 12
        assert len(report.trials) == 6  # 2 tasks x 3 reps

    async def test_report_has_summary(self):
        tasks = [AgentTask(name="t", prompt="P", expected_tool="x")]
        schemas = [
            {
                "name": "x",
                "description": "X",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        filtered = {"t": schemas}

        async def mock_loop(task_prompt, tools, mock_executor, **kwargs):
            return _make_metrics(task_name="t", tool_count=len(tools))

        with patch("tcp.agent.benchmark.run_agent_loop", side_effect=mock_loop):
            report = await run_paired_benchmark(
                tasks=tasks,
                corpus_schemas=schemas,
                filtered_schemas_by_task=filtered,
                repetitions=1,
            )

        assert isinstance(report.summary, dict)
        assert report.summary["trial_count"] == 1
