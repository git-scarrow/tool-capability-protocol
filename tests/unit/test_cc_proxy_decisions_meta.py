"""Decisions log fields expected by shadow pilot tooling."""

from __future__ import annotations

import difflib
from unittest.mock import patch

from tcp.proxy.cc_proxy import (
    DECISION_LOG_SCHEMA,
    EXPECTED_TOOL_DERIVATION_ALGORITHM,
    _compute_expected_tool_name,
    _process_tools_array,
    _write_decision_record,
)


def test_decisions_meta_includes_full_tool_count_and_survivor_count() -> None:
    tools = [
        {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
        {"name": "Bash", "description": "shell", "input_schema": {"type": "object"}},
    ]
    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "show me the README"}],
            }
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
            {
                "role": "user",
                "content": [{"type": "text", "text": "read the config file"}],
            }
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


def test_description_similarity_defaults_to_deferred(monkeypatch) -> None:
    monkeypatch.delenv("TCP_CC_DESC_SIM_MODE", raising=False)
    tools = [
        {
            "name": f"Tool{i}",
            "description": "similar description " * 20,
            "input_schema": {"type": "object"},
        }
        for i in range(10)
    ]
    _, meta = _process_tools_array(tools, {"messages": []}, "shadow")
    assert meta["description_similarity_max"] is None
    assert meta["description_similarity_max_status"] == "deferred"
    assert meta["description_similarity_max_method"] == "difflib_v1"
    assert meta["description_similarity_max_pair_count"] == 45
    assert meta["description_similarity_max_input_count"] == 10


def test_description_similarity_inline_exact_for_small_inputs(monkeypatch) -> None:
    monkeypatch.setenv("TCP_CC_DESC_SIM_MODE", "inline")
    tools = [
        {
            "name": "ToolA",
            "description": "read a file from disk",
            "input_schema": {"type": "object"},
        },
        {
            "name": "ToolB",
            "description": "read a file from disk safely",
            "input_schema": {"type": "object"},
        },
    ]
    _, meta = _process_tools_array(tools, {"messages": []}, "shadow")
    assert isinstance(meta["description_similarity_max"], float)
    assert 0.0 <= meta["description_similarity_max"] <= 1.0
    assert meta["description_similarity_max_status"] == "exact"


def test_description_similarity_inline_skips_large_budget(monkeypatch) -> None:
    monkeypatch.setenv("TCP_CC_DESC_SIM_MODE", "inline")

    def fail_sequence_matcher(*args, **kwargs):
        raise AssertionError("full pairwise SequenceMatcher should be budget-skipped")

    monkeypatch.setattr(difflib, "SequenceMatcher", fail_sequence_matcher)
    tools = [
        {
            "name": f"Tool{i}",
            "description": "long repeated description " * 80,
            "input_schema": {"type": "object"},
        }
        for i in range(100)
    ]
    _, meta = _process_tools_array(tools, {"messages": []}, "shadow")
    assert meta["description_similarity_max"] is None
    assert meta["description_similarity_max_status"] == "skipped_budget"
    assert meta["description_similarity_max_pair_count"] == 4950


def test_prompt_similarity_caps_large_prompt_and_descriptions() -> None:
    prompt = "system reminder " * 1000 + " please read the project status"
    tools = [
        {
            "name": f"Tool{i}",
            "description": "long repeated description " * 500,
            "input_schema": {"type": "object"},
        }
        for i in range(50)
    ]
    _, meta = _process_tools_array(
        tools,
        {"messages": [{"role": "user", "content": prompt}]},
        "shadow",
    )
    assert "top_survivor_by_similarity" in meta
    assert meta["top_survivor_by_similarity_status"] == "capped"
    assert meta["top_survivor_by_similarity_method"] == "difflib_capped_v1"
    assert meta["top_survivor_by_similarity_prompt_chars"] == len(prompt)
    assert meta["top_survivor_by_similarity_tool_count"] == 50


# ── TCP-IMP-23: decision log schema versioning and derivation invariants ───────
# Forge invariants: these tests prevent MT-21 from running on semantically false
# data (Counterexample Forge session, 2026-05-09).


def test_decision_log_schema_is_present_in_written_record() -> None:
    """decision_log_schema must be present on every written record (IMP-23)."""
    captured: list[dict] = []
    with patch("tcp.proxy.cc_proxy._append_jsonl", lambda _path, rec: captured.append(rec)):
        _write_decision_record(0.0, {"survivor_count": 0, "survivor_names_sorted": []}, None)
    assert len(captured) == 1
    assert captured[0]["decision_log_schema"] == DECISION_LOG_SCHEMA
    assert isinstance(captured[0]["decision_log_schema"], int)


def test_derivation_algorithm_version_is_present_in_written_record() -> None:
    """expected_tool_derivation_algorithm must be present on every written record."""
    captured: list[dict] = []
    with patch("tcp.proxy.cc_proxy._append_jsonl", lambda _path, rec: captured.append(rec)):
        _write_decision_record(0.0, {"survivor_count": 1, "survivor_names_sorted": ["Read"]}, None)
    assert captured[0]["expected_tool_derivation_algorithm"] == EXPECTED_TOOL_DERIVATION_ALGORITHM


def test_derivation_single_survivor_emits_with_source() -> None:
    """Single survivor → expected_tool_name emitted, source='single_survivor', no abstain."""
    result = _compute_expected_tool_name({"survivor_count": 1, "survivor_names_sorted": ["Read"]})
    assert result.expected_tool_name == "Read"
    assert result.derivation_source == "single_survivor"
    assert result.candidate_set_size == 1
    assert result.abstain_reason is None


def test_derivation_multiple_survivors_abstains() -> None:
    """Multiple survivors → abstains with ambiguous_N_survivors, no source."""
    result = _compute_expected_tool_name(
        {"survivor_count": 3, "survivor_names_sorted": ["Read", "Bash", "Grep"]}
    )
    assert result.expected_tool_name is None
    assert result.derivation_source is None
    assert result.abstain_reason == "ambiguous_3_survivors"


def test_derivation_no_survivors_abstains() -> None:
    """Zero survivors → abstains with 'no_survivors'."""
    result = _compute_expected_tool_name({"survivor_count": 0, "survivor_names_sorted": []})
    assert result.expected_tool_name is None
    assert result.abstain_reason == "no_survivors"


def test_derivation_count_list_mismatch_abstains() -> None:
    """count=1 but empty survivors list → abstains with 'count_list_mismatch'."""
    result = _compute_expected_tool_name({"survivor_count": 1, "survivor_names_sorted": []})
    assert result.expected_tool_name is None
    assert result.abstain_reason == "count_list_mismatch"


def test_derivation_no_meta_abstains() -> None:
    """None meta → abstains with 'no_meta' (not raised, not masked)."""
    result = _compute_expected_tool_name(None)
    assert result.expected_tool_name is None
    assert result.abstain_reason == "no_meta"


def test_derivation_source_null_iff_name_null() -> None:
    """Forge invariant: derivation_source is null iff expected_tool_name is null."""
    cases = [
        {"survivor_count": 1, "survivor_names_sorted": ["Read"]},
        {"survivor_count": 0, "survivor_names_sorted": []},
        {"survivor_count": 2, "survivor_names_sorted": ["Read", "Bash"]},
        {"survivor_count": 1, "survivor_names_sorted": []},
    ]
    for meta in cases:
        r = _compute_expected_tool_name(meta)
        if r.expected_tool_name is None:
            assert r.derivation_source is None, f"source should be null when name is null: {meta}"
        else:
            assert r.derivation_source is not None, f"source must be set when name is emitted: {meta}"


def test_derivation_abstain_reason_null_iff_emitted() -> None:
    """Forge invariant: abstain_reason is null iff expected_tool_name is not null."""
    cases = [
        {"survivor_count": 1, "survivor_names_sorted": ["Bash"]},
        {"survivor_count": 0, "survivor_names_sorted": []},
        {"survivor_count": 4, "survivor_names_sorted": ["A", "B", "C", "D"]},
        None,
    ]
    for meta in cases:
        r = _compute_expected_tool_name(meta)
        if r.expected_tool_name is not None:
            assert r.abstain_reason is None, f"abstain_reason must be null when tool emitted: {meta}"
        else:
            assert r.abstain_reason is not None, f"abstain_reason required when abstained: {meta}"


def test_decision_log_schema_candidate_set_phase_present() -> None:
    """expected_tool_candidate_set_phase must be logged to disambiguate pipeline stage."""
    captured: list[dict] = []
    with patch("tcp.proxy.cc_proxy._append_jsonl", lambda _path, rec: captured.append(rec)):
        _write_decision_record(0.0, {"survivor_count": 1, "survivor_names_sorted": ["Read"]}, "Read")
    assert captured[0]["expected_tool_candidate_set_phase"] == "post_stage4_survivors"
