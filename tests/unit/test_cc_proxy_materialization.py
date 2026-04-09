"""TCP-IMP-12: Pack-state-aware schema materialization."""

from __future__ import annotations

from tcp.proxy.cc_proxy import _process_tools_array


def _tool_by_name(tools: list[dict], name: str) -> dict:
    for tool in tools:
        if tool["name"] == name:
            return tool
    raise AssertionError(f"tool not found: {name}")


RICH_TOOL_SET = [
    {
        "name": "mcp__filesystem__read_file",
        "description": "Read a file from disk with an explicit absolute path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "head": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "mcp__bay-view-graph__list_emails",
        "description": "Query Bay View graph mail for matching messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "after": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
]


def test_active_pack_keeps_full_schema_while_workspace_allow_is_deferred(monkeypatch) -> None:
    monkeypatch.setenv("TCP_PROXY_WORKSPACE_MCP_SERVERS", "bay-view-graph")
    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Check email from Jason from today"}],
            }
        ]
    }

    result_tools, meta = _process_tools_array(RICH_TOOL_SET, body, "live")

    fs_tool = _tool_by_name(result_tools, "mcp__filesystem__read_file")
    deferred_tool = _tool_by_name(result_tools, "mcp__bay-view-graph__list_emails")

    assert fs_tool["input_schema"]["properties"]["path"]["type"] == "string"
    assert deferred_tool["input_schema"]["properties"] == {}
    assert deferred_tool["input_schema"]["additionalProperties"] is True
    assert deferred_tool["description"].startswith("Deferred schema for")
    assert "mcp__filesystem__read_file" in meta["materialized_schema_tools"]
    assert "mcp__bay-view-graph__list_emails" in meta["deferred_schema_tools"]
    assert meta["surface_state_by_tool"]["mcp__filesystem__read_file"] == "active"
    assert meta["surface_state_by_tool"]["mcp__bay-view-graph__list_emails"] == "deferred"
    assert meta["tool_surface_bytes_after"] < meta["tool_surface_bytes_before"]


def test_workspace_profile_promotes_workspace_critical_pack_to_full_schema(monkeypatch) -> None:
    monkeypatch.setenv("TCP_PROXY_WORKSPACE_PROFILE", "bay-view")
    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Check email from Jason from today"}],
            }
        ]
    }

    result_tools, meta = _process_tools_array(RICH_TOOL_SET, body, "live")
    bay_tool = _tool_by_name(result_tools, "mcp__bay-view-graph__list_emails")

    assert bay_tool["input_schema"]["properties"]["query"]["type"] == "string"
    assert "mcp__bay-view-graph__list_emails" in meta["materialized_schema_tools"]
    assert "mcp__bay-view-graph__list_emails" not in meta["deferred_schema_tools"]
    assert meta["surface_state_by_tool"]["mcp__bay-view-graph__list_emails"] == "active"


def test_explicitly_rescued_server_stays_visible_but_deferred(monkeypatch) -> None:
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_MCP_SERVERS", raising=False)
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_PROFILE", raising=False)
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "use mcp__bay-view-graph__list_emails to check email from Jason",
                    }
                ],
            }
        ]
    }

    result_tools, meta = _process_tools_array(RICH_TOOL_SET, body, "live")
    bay_tool = _tool_by_name(result_tools, "mcp__bay-view-graph__list_emails")

    assert bay_tool["description"].startswith("Deferred schema for")
    assert bay_tool["input_schema"]["additionalProperties"] is True
    assert "mcp__bay-view-graph__list_emails" in meta["explicit_server_rescued"]
    assert "mcp__bay-view-graph__list_emails" in meta["deferred_schema_tools"]
    assert meta["surface_state_by_tool"]["mcp__bay-view-graph__list_emails"] == "deferred"


def test_suppressed_pack_remains_hidden_without_workspace_allow(monkeypatch) -> None:
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_MCP_SERVERS", raising=False)
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_PROFILE", raising=False)
    monkeypatch.delenv("TCP_PROXY_PROFILE", raising=False)
    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Read the config file"}],
            }
        ]
    }

    result_tools, meta = _process_tools_array(RICH_TOOL_SET, body, "live")
    names = {tool["name"] for tool in result_tools}

    assert "mcp__filesystem__read_file" in names
    assert "mcp__bay-view-graph__list_emails" not in names
    assert "mcp__bay-view-graph__list_emails" in meta["server_filtered"]
