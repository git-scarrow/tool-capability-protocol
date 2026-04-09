"""Decisions log fields expected by shadow pilot tooling."""

from __future__ import annotations

from tcp.proxy.cc_proxy import _process_tools_array


def test_decisions_meta_includes_full_tool_count_and_survivor_count() -> None:
    tools = [
        {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
        {"name": "Bash", "description": "shell", "input_schema": {"type": "object"}},
    ]
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "show me the README"}]}
        ],
    }
    _, meta = _process_tools_array(tools, body, "shadow")
    assert meta["full_tool_count"] == meta["tool_count_before"] == 2
    assert "survivor_count" in meta
    assert isinstance(meta["survivor_count"], int)
    assert meta["survivor_count"] == len(meta["survivor_names_sorted"])


def test_decisions_meta_includes_replay_freshness_fields() -> None:
    tools = [
        {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
        {
            "name": "mcp__filesystem__read_file",
            "description": "read file",
            "input_schema": {"type": "object"},
        },
    ]
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "read the config file"}]}
        ],
    }
    _, meta = _process_tools_array(tools, body, "live")
    assert meta["prompt_hash"]
    assert meta["workspace_path"]
    assert meta["workspace_name"]
    assert meta["resolved_profile"]
    assert meta["pack_manifest_source"]
    assert meta["pack_manifest_hash"]
    assert "hard_allowed_servers" in meta
