"""Instrumented async agent loop for EXP-2 benchmarking.

Executes a single task against the Anthropic Messages API, collects
timing and token metrics at every API call boundary, and dispatches
tool calls to a mock executor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import anthropic


@dataclass(frozen=True)
class LoopMetrics:
    """Timing and correctness metrics from a single agent loop run."""

    task_name: str
    tool_count: int
    turns: int
    first_token_latency_ms: float
    total_response_time_ms: float
    input_tokens: int
    output_tokens: int
    tools_called: tuple[str, ...]
    selected_tool_correct: bool
    error: str | None


async def run_agent_loop(
    task_prompt: str,
    tools: list[dict],
    mock_executor: Callable[[str, dict], str],
    *,
    expected_tool: str | None,
    task_name: str,
    model: str = "claude-sonnet-4-6",
    max_turns: int = 5,
) -> LoopMetrics:
    """Execute a single agent loop and return metrics.

    1. Call messages.create() with task_prompt and tools
    2. If response contains tool_use, dispatch to mock_executor
    3. Feed tool_result back, repeat until text-only or max_turns
    4. Collect timing at every API call boundary
    """
    client = anthropic.AsyncAnthropic()
    messages: list[dict] = [{"role": "user", "content": task_prompt}]
    tools_called: list[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    first_token_latency_ms = 0.0
    turns = 0

    total_start = time.perf_counter_ns()

    try:
        for turn_idx in range(max_turns):
            call_start = time.perf_counter_ns()
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                messages=messages,
                tools=tools,
            )
            call_end = time.perf_counter_ns()

            turns += 1

            if turn_idx == 0:
                first_token_latency_ms = (call_end - call_start) / 1_000_000

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Extract tool_use blocks
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                break

            # Dispatch each tool call to mock executor
            tool_results = []
            for block in tool_use_blocks:
                tools_called.append(block.name)
                result = mock_executor(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

    except Exception as exc:
        total_end = time.perf_counter_ns()
        return LoopMetrics(
            task_name=task_name,
            tool_count=len(tools),
            turns=turns,
            first_token_latency_ms=first_token_latency_ms,
            total_response_time_ms=(total_end - total_start) / 1_000_000,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            tools_called=tuple(tools_called),
            selected_tool_correct=False,
            error=str(exc),
        )

    total_end = time.perf_counter_ns()

    # Correctness: check if the first tool called matches expected
    first_tool = tools_called[0] if tools_called else None
    if expected_tool is None:
        correct = first_tool is None
    else:
        correct = first_tool == expected_tool

    return LoopMetrics(
        task_name=task_name,
        tool_count=len(tools),
        turns=turns,
        first_token_latency_ms=first_token_latency_ms,
        total_response_time_ms=(total_end - total_start) / 1_000_000,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        tools_called=tuple(tools_called),
        selected_tool_correct=correct,
        error=None,
    )
