from __future__ import annotations

import json

from scripts.shadow_analysis import (
    analyse_session,
    _decision_has_freshness_context,
    _load_decision_index,
    _match_decision_record,
    _prompt_hash,
)
from tcp.proxy.tool_flag_map import build_static_inventory


def test_build_static_inventory_is_nontrivial() -> None:
    inventory = build_static_inventory()
    assert inventory["version"] == "2.0"
    tool_names = {tool["name"] for tool in inventory["tools"]}
    assert "Read" in tool_names
    assert "mcp__notion-agents__chat_with_agent" in tool_names
    assert "mcp__bay-view-graph__search_emails" in tool_names


def test_decision_has_freshness_context_requires_captured_fields() -> None:
    assert not _decision_has_freshness_context({"workspace_path": "/tmp"})
    assert _decision_has_freshness_context(
        {
            "workspace_path": "/tmp",
            "prompt_hash": "abc",
            "pack_manifest_source": "/tmp/.tcp-proxy-packs.yaml",
            "pack_manifest_hash": "def",
            "resolved_profile": "default",
            "pack_states": {"core-coding": "active"},
            "survivor_names_sorted": ["Read"],
        }
    )


def test_match_decision_record_uses_workspace_and_prompt_hash(monkeypatch, tmp_path) -> None:
    prompt = "read the config file"
    prompt_hash = _prompt_hash(prompt)
    rows = {
        ("/repo", prompt_hash): [
            {"workspace_path": "/repo", "prompt_hash": prompt_hash, "ts": 10.0},
            {"workspace_path": "/repo", "prompt_hash": prompt_hash, "ts": 15.0},
        ]
    }
    _load_decision_index.cache_clear()
    monkeypatch.setattr("scripts.shadow_analysis._load_decision_index", lambda: rows)
    matched = _match_decision_record(
        workspace_path="/repo",
        prompt_hash=prompt_hash,
        prompt_ts=14.0,
    )
    assert matched is not None
    assert matched["ts"] == 15.0


def test_analyse_session_uses_captured_inventory_and_turn_context(
    monkeypatch, tmp_path
) -> None:
    sessions_dir = tmp_path / "sessions"
    inventories_dir = tmp_path / "inventories"
    proxy_dir = tmp_path / "proxy"
    sessions_dir.mkdir()
    inventories_dir.mkdir()
    proxy_dir.mkdir()

    inventory = {
        "version": "2.0",
        "tools": [
            {"name": "Read", "flags": 1},
            {"name": "mcp__filesystem__read_file", "flags": 1},
        ],
    }
    inventory_id = "inv123"
    (inventories_dir / f"{inventory_id}.json").write_text(json.dumps(inventory))

    prompt = "read the config file"
    session_path = sessions_dir / "sess.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "session_start",
                        "session_id": "sess",
                        "timestamp": 10.0,
                        "permission_mode": "default",
                        "cwd": "/repo",
                        "inventory_snapshot_id": inventory_id,
                    }
                ),
                json.dumps(
                    {
                        "event": "user_prompt",
                        "session_id": "sess",
                        "turn_id": 1,
                        "timestamp": 11.0,
                        "prompt": prompt,
                    }
                ),
                json.dumps(
                    {
                        "event": "tool_use",
                        "session_id": "sess",
                        "turn_id": 1,
                        "call_id": "call1",
                        "tool_use_id": "toolu_1",
                        "tool_name": "Read",
                        "tool_result_status": "ok",
                        "timestamp": 12.0,
                    }
                ),
            ]
        )
        + "\n"
    )

    (proxy_dir / "decisions.jsonl").write_text(
        json.dumps(
            {
                "ts": 11.5,
                "workspace_path": "/repo",
                "prompt_hash": _prompt_hash(prompt),
                "pack_manifest_source": "/repo/.tcp-proxy-packs.yaml",
                "pack_manifest_hash": "hash123",
                "resolved_profile": "default",
                "pack_states": {"core-coding": "active"},
                "survivor_names_sorted": ["Read", "mcp__filesystem__read_file"],
            }
        )
        + "\n"
    )

    monkeypatch.setattr("scripts.shadow_analysis.INVENTORIES_DIR", inventories_dir)
    monkeypatch.setattr("scripts.shadow_analysis.DECISIONS_LOG", proxy_dir / "decisions.jsonl")
    _load_decision_index.cache_clear()

    results = analyse_session(session_path)
    assert len(results) == 1
    result = results[0]
    assert result.benchmark_eligible is True
    assert result.exclusion_reason is None
    assert result.in_survivor_set is True
