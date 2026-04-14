"""Tests for the instrumented agent loop.

All tests mock the Anthropic API -- no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from tcp.agent.loop import ErrorKind, LoopMetrics, run_agent_loop


# --- Test fixtures ---


def _make_usage(input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    return usage


def _make_text_block(text: str = "Done.") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(
    name: str = "fs-read-file",
    tool_input: dict | None = None,
    tool_id: str = "toolu_123",
) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = tool_input or {"input": "test"}
    block.id = tool_id
    return block


def _make_response(
    content: list,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.usage = _make_usage(input_tokens, output_tokens)
    return resp


def _noop_executor(tool_name: str, tool_input: dict) -> str:
    return '{"status": "ok"}'


# --- Tests ---


class TestLoopMetrics:
    """LoopMetrics is a frozen dataclass."""

    def test_frozen(self):
        m = LoopMetrics(
            task_name="test",
            tool_count=10,
            turns=1,
            first_token_latency_ms=50.0,
            total_response_time_ms=100.0,
            input_tokens=200,
            output_tokens=50,
            tools_called=("fs-read-file",),
            selected_tool_correct=True,
            error=None,
        )
        with pytest.raises(AttributeError):
            m.task_name = "changed"  # type: ignore[misc]

    def test_fields(self):
        m = LoopMetrics(
            task_name="t",
            tool_count=5,
            turns=2,
            first_token_latency_ms=10.0,
            total_response_time_ms=20.0,
            input_tokens=100,
            output_tokens=50,
            tools_called=("a", "b"),
            selected_tool_correct=True,
            error=None,
        )
        assert m.turns == 2
        assert m.tools_called == ("a", "b")

    def test_new_routing_fields_default(self):
        m = LoopMetrics(
            task_name="t",
            tool_count=5,
            turns=1,
            first_token_latency_ms=10.0,
            total_response_time_ms=20.0,
            input_tokens=100,
            output_tokens=50,
            tools_called=(),
            selected_tool_correct=True,
            error=None,
        )
        assert m.route_confidence == ""
        assert m.survivor_count == 0

    def test_mt12_fields_default(self):
        """TCP-MT-12 telemetry fields default to None/False/0.0."""
        m = LoopMetrics(
            task_name="t",
            tool_count=2,
            turns=1,
            first_token_latency_ms=10.0,
            total_response_time_ms=20.0,
            input_tokens=100,
            output_tokens=50,
            tools_called=("tool-a",),
            selected_tool_correct=True,
            error=None,
        )
        assert m.first_tool_name is None
        assert m.expected_tool_name is None
        assert m.retry_latency_penalty_ms == 0.0
        assert m.description_similarity_max == 0.0
        assert m.ambiguous_lane is False
        assert m.pack_promotion_triggered is False
        assert m.schema_load_on_demand is False


@pytest.mark.asyncio
class TestRunAgentLoop:
    """Test the agent loop with mocked Anthropic API."""

    async def test_single_turn_no_tool_use(self):
        """Model responds with text only -- no tool calls."""
        mock_response = _make_response(
            content=[_make_text_block("I can help with that.")],
            input_tokens=150,
            output_tokens=30,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Hello",
                tools=[
                    {
                        "name": "test",
                        "description": "t",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="test-task",
            )

        assert metrics.turns == 1
        assert metrics.tools_called == ()
        assert metrics.input_tokens == 150
        assert metrics.output_tokens == 30
        assert metrics.first_token_latency_ms > 0
        assert metrics.total_response_time_ms >= metrics.first_token_latency_ms
        assert metrics.selected_tool_correct is True
        assert metrics.error is None

    async def test_single_tool_call(self):
        """Model calls one tool then finishes."""
        tool_response = _make_response(
            content=[_make_tool_use_block("fs-read-file", {"input": "/tmp/x"})],
            input_tokens=200,
            output_tokens=40,
        )
        final_response = _make_response(
            content=[_make_text_block("Here are the contents.")],
            input_tokens=250,
            output_tokens=20,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Read /tmp/x",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="read-test",
            )

        assert metrics.turns == 2
        assert metrics.tools_called == ("fs-read-file",)
        assert metrics.input_tokens == 450  # 200 + 250
        assert metrics.output_tokens == 60  # 40 + 20
        assert metrics.selected_tool_correct is True

    async def test_wrong_tool_selected(self):
        """Model calls a different tool than expected."""
        tool_response = _make_response(
            content=[_make_tool_use_block("git-status")],
            input_tokens=100,
            output_tokens=30,
        )
        final_response = _make_response(
            content=[_make_text_block("Done.")],
            input_tokens=120,
            output_tokens=10,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Read file",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="wrong-tool",
            )

        assert metrics.selected_tool_correct is False
        assert metrics.tools_called == ("git-status",)

    async def test_max_turns_respected(self):
        """Loop stops after max_turns even if model keeps calling tools."""
        tool_response = _make_response(
            content=[_make_tool_use_block("fs-read-file")],
            input_tokens=100,
            output_tokens=20,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=tool_response)

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Loop forever",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="max-turns",
                max_turns=3,
            )

        assert metrics.turns == 3

    async def test_api_error_captured(self):
        """API errors are captured in the error field, not raised."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=Exception("rate limited")
        )

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Fail",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="error-test",
            )

        assert metrics.error is not None
        assert "rate limited" in metrics.error
        assert metrics.turns == 0
        assert metrics.error_kind == "program_bug"

    async def test_auth_error_classified(self):
        """AuthenticationError gets API_AUTH classification."""
        import httpx

        mock_client = AsyncMock()
        mock_response = httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com"))
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="invalid api key",
                response=mock_response,
                body=None,
            )
        )

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Fail",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="auth-test",
            )

        assert metrics.error_kind == "api_auth"
        assert metrics.error is not None

    async def test_bad_request_classified(self):
        """BadRequestError gets API_BAD_REQUEST classification."""
        import httpx

        mock_client = AsyncMock()
        mock_response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com"))
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.BadRequestError(
                message="invalid tool schema",
                response=mock_response,
                body=None,
            )
        )

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Fail",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="bad-request-test",
            )

        assert metrics.error_kind == "api_bad_request"
        assert metrics.error is not None

    async def test_tool_count_from_tools_list(self):
        """tool_count reflects the number of tools provided."""
        mock_response = _make_response(content=[_make_text_block("ok")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        tools = [
            {
                "name": f"tool-{i}",
                "description": f"t{i}",
                "input_schema": {"type": "object", "properties": {}},
            }
            for i in range(15)
        ]

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Hi",
                tools=tools,
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="count-test",
            )

        assert metrics.tool_count == 15


@pytest.mark.asyncio
class TestMT12Telemetry:
    """TCP-MT-12: first-tool-miss telemetry fields."""

    async def test_first_tool_name_and_expected_populated(self):
        """first_tool_name and expected_tool_name are set on a successful run."""
        tool_response = _make_response(
            content=[_make_tool_use_block("fs-read-file")],
        )
        final_response = _make_response(content=[_make_text_block("Done.")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Read a file",
                tools=[
                    {"name": "fs-read-file", "description": "Reads a file from disk"},
                    {"name": "fs-write-file", "description": "Writes content to a file"},
                ],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="mt12-names",
            )

        assert metrics.first_tool_name == "fs-read-file"
        assert metrics.expected_tool_name == "fs-read-file"
        assert metrics.selected_tool_correct is True
        assert metrics.retry_latency_penalty_ms == 0.0

    async def test_retry_penalty_nonzero_on_miss(self):
        """retry_latency_penalty_ms > 0 when first tool is wrong and has multi-turn."""
        wrong_tool_response = _make_response(
            content=[_make_tool_use_block("git-status")],
        )
        right_tool_response = _make_response(
            content=[_make_tool_use_block("fs-read-file")],
        )
        final_response = _make_response(content=[_make_text_block("Done.")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[wrong_tool_response, right_tool_response, final_response]
        )

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Read a file",
                tools=[
                    {"name": "fs-read-file", "description": "Reads a file from disk"},
                    {"name": "git-status", "description": "Shows git repository status"},
                ],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="mt12-retry",
            )

        assert metrics.first_tool_name == "git-status"
        assert metrics.expected_tool_name == "fs-read-file"
        assert metrics.selected_tool_correct is False
        assert metrics.retry_latency_penalty_ms > 0.0

    async def test_ambiguous_lane_true_when_multiple_tools(self):
        """ambiguous_lane is True when ≥2 tools are provided."""
        mock_response = _make_response(content=[_make_text_block("ok")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Hi",
                tools=[
                    {"name": "tool-a", "description": "Does thing A"},
                    {"name": "tool-b", "description": "Does thing B"},
                ],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="mt12-ambig",
            )

        assert metrics.ambiguous_lane is True

    async def test_ambiguous_lane_false_single_tool(self):
        """ambiguous_lane is False with only one tool."""
        mock_response = _make_response(content=[_make_text_block("ok")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Hi",
                tools=[{"name": "tool-a", "description": "Does thing A"}],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="mt12-single",
            )

        assert metrics.ambiguous_lane is False

    async def test_description_similarity_max_nonzero_for_similar_tools(self):
        """description_similarity_max > 0 for tools with similar descriptions."""
        mock_response = _make_response(content=[_make_text_block("ok")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Hi",
                tools=[
                    {"name": "tool-a", "description": "Reads text files from disk"},
                    {"name": "tool-b", "description": "Reads binary files from disk"},
                ],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="mt12-sim",
            )

        assert metrics.description_similarity_max > 0.0

    async def test_pack_promotion_and_schema_load_passthrough(self):
        """pack_promotion_triggered and schema_load_on_demand are passed through."""
        mock_response = _make_response(content=[_make_text_block("ok")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Hi",
                tools=[{"name": "t", "description": "t", "input_schema": {"type": "object", "properties": {}}}],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="mt12-flags",
                pack_promotion_triggered=True,
                schema_load_on_demand=True,
            )

        assert metrics.pack_promotion_triggered is True
        assert metrics.schema_load_on_demand is True


@pytest.mark.asyncio
class TestBypassPath:
    """Deterministic bypass skips the LLM entirely."""

    async def test_bypass_invokes_executor_directly(self):
        """When bypass_tool is provided, no API call is made."""
        executor_calls = []

        def tracking_executor(tool_name: str, tool_input: dict) -> str:
            executor_calls.append(tool_name)
            return '{"status": "ok"}'

        metrics = await run_agent_loop(
            task_prompt="Do something",
            tools=[],
            mock_executor=tracking_executor,
            expected_tool="my-tool",
            task_name="bypass-test",
            bypass_tool="my-tool",
        )

        assert metrics.llm_bypassed is True
        assert metrics.tools_called == ("my-tool",)
        assert metrics.selected_tool_correct is True
        assert metrics.turns == 0
        assert metrics.input_tokens == 0
        assert executor_calls == ["my-tool"]

    async def test_bypass_wrong_tool_still_correct(self):
        """Bypass tool matches expected_tool — correctness is True."""
        metrics = await run_agent_loop(
            task_prompt="Do something",
            tools=[],
            mock_executor=_noop_executor,
            expected_tool="my-tool",
            task_name="bypass-match",
            bypass_tool="my-tool",
        )
        assert metrics.selected_tool_correct is True
        assert metrics.llm_bypassed is True

    async def test_no_bypass_when_not_specified(self):
        """Without bypass_tool, the normal LLM path runs."""
        mock_response = _make_response(
            content=[_make_text_block("ok")],
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Hello",
                tools=[{"name": "t", "description": "t", "input_schema": {"type": "object", "properties": {}}}],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="no-bypass",
            )

        assert metrics.llm_bypassed is False
        mock_client.messages.create.assert_called_once()
