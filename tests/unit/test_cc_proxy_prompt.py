"""Tests for TCP-CC proxy prompt extraction."""

from __future__ import annotations

from tcp.proxy.prompt_select import extract_task_prompt


def test_extract_task_prompt_last_user_with_text() -> None:
    messages = [
        {"role": "user", "content": "First task"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "r"}],
        },
        {"role": "user", "content": [{"type": "text", "text": "Second task"}]},
    ]
    assert extract_task_prompt(messages) == "Second task"


def test_extract_task_prompt_skips_user_without_text() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Real prompt"}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "y", "content": "z"}],
        },
    ]
    assert extract_task_prompt(messages) == "Real prompt"


def test_extract_task_prompt_string_content() -> None:
    messages = [{"role": "user", "content": "Plain string"}]
    assert extract_task_prompt(messages) == "Plain string"
