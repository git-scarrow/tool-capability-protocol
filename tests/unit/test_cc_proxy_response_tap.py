"""Tests for TCP-IMP-14 / TCP-IMP-21: response-tap helpers in cc_proxy.

Covers:
 - SSE first-tool extraction (normal, cross-chunk-boundary, no-tool)
 - SSE all-tools extraction (multi-tool, ordering, message_stop gating)
 - Non-streamed JSON extraction (first-tool and all-tools)
 - expected_tool_name derivation
 - first_tool_correct computation
 - decisions.jsonl enrichment (one record per turn)
 - backward compatibility (new fields present with correct types)
 - tool_call_sequence retention: second and later tool calls are not dropped
 - latency guard (tap overhead is negligible)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tcp.proxy.cc_proxy import (
    _ExpectedToolDerivation,
    _all_tools_from_response_body,
    _all_tools_from_sse_buf,
    _compute_expected_tool_name,
    _first_tool_from_response_body,
    _first_tool_from_sse_buf,
    _top_survivor_by_prompt_similarity,
    _write_decision_record,
)

# ── SSE chunk helpers ──────────────────────────────────────────────────────────


def _sse_content_block_start(name: str, index: int = 0) -> bytes:
    data = json.dumps(
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": name,
                "input": {},
            },
        }
    )
    return f"event: content_block_start\ndata: {data}\n\n".encode()


def _sse_text_block_start(index: int = 0) -> bytes:
    data = json.dumps(
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        }
    )
    return f"event: content_block_start\ndata: {data}\n\n".encode()


def _sse_message_stop() -> bytes:
    data = json.dumps({"type": "message_stop"})
    return f"event: message_stop\ndata: {data}\n\n".encode()


def _sse_message_start() -> bytes:
    data = json.dumps({"type": "message_start", "message": {"role": "assistant"}})
    return f"event: message_start\ndata: {data}\n\n".encode()


# ── Tests: _first_tool_from_sse_buf ───────────────────────────────────────────


class TestFirstToolFromSseBuf:
    def test_single_chunk_with_tool_use(self):
        buf = _sse_message_start() + _sse_content_block_start("Bash")
        tool, ended = _first_tool_from_sse_buf(buf)
        assert tool == "Bash"
        assert ended is False

    def test_text_block_then_tool_block(self):
        buf = _sse_text_block_start(0) + _sse_content_block_start(
            "mcp__fs__read_file", index=1
        )
        tool, ended = _first_tool_from_sse_buf(buf)
        # text block is not tool_use; tool block is second
        assert tool == "mcp__fs__read_file"
        assert ended is False

    def test_no_tool_call_message_stop(self):
        buf = _sse_message_start() + _sse_text_block_start(0) + _sse_message_stop()
        tool, ended = _first_tool_from_sse_buf(buf)
        assert tool is None
        assert ended is True

    def test_empty_buffer(self):
        tool, ended = _first_tool_from_sse_buf(b"")
        assert tool is None
        assert ended is False

    def test_partial_data_line_not_parsed(self):
        """Incomplete final line is skipped gracefully (no crash, no false positive)."""
        full = _sse_content_block_start("Bash")
        # Truncate midway through the last data line
        partial = full[: len(full) // 2]
        tool, ended = _first_tool_from_sse_buf(partial)
        # May or may not find tool depending on where truncation lands,
        # but must not raise.
        assert isinstance(tool, (str, type(None)))
        assert isinstance(ended, bool)

    def test_tool_use_spans_chunk_boundary(self):
        """Tool name arrives split across two chunks — combine and detect."""
        full_chunk = _sse_content_block_start("my_special_tool")
        split = len(full_chunk) // 2
        chunk1 = full_chunk[:split]
        chunk2 = full_chunk[split:]

        # First chunk alone: might not have the full JSON
        tool1, ended1 = _first_tool_from_sse_buf(chunk1)
        # Second chunk combined: must detect the tool
        tool2, ended2 = _first_tool_from_sse_buf(chunk1 + chunk2)
        assert tool2 == "my_special_tool"
        assert ended2 is False

    def test_non_utf8_bytes_do_not_raise(self):
        buf = b"\xff\xfe" + _sse_content_block_start("safe_tool")
        tool, ended = _first_tool_from_sse_buf(buf)
        # Corrupted prefix shouldn't prevent parsing of valid subsequent lines
        assert isinstance(tool, (str, type(None)))

    def test_malformed_json_skipped(self):
        malformed = b"event: content_block_start\ndata: {not valid json}\n\n"
        good = _sse_content_block_start("real_tool")
        tool, ended = _first_tool_from_sse_buf(malformed + good)
        assert tool == "real_tool"


# ── Tests: _all_tools_from_sse_buf ────────────────────────────────────────────


class TestAllToolsFromSseBuf:
    """TCP-IMP-21: all-tools SSE extraction retains second and later tool calls."""

    def test_single_tool(self):
        buf = _sse_message_start() + _sse_content_block_start("Bash")
        tools, ended = _all_tools_from_sse_buf(buf)
        assert [t["tool_name"] for t in tools] == ["Bash"]
        assert ended is False

    def test_two_tools_both_retained(self):
        """Second tool call must not be dropped — the core TCP-IMP-21 regression."""
        buf = (
            _sse_message_start()
            + _sse_content_block_start("Read", index=0)
            + _sse_content_block_start("Write", index=1)
            + _sse_message_stop()
        )
        tools, ended = _all_tools_from_sse_buf(buf)
        assert [t["tool_name"] for t in tools] == ["Read", "Write"]
        assert ended is True

    def test_three_tools_in_order(self):
        buf = (
            _sse_content_block_start("tool_a", index=0)
            + _sse_content_block_start("tool_b", index=2)
            + _sse_content_block_start("tool_c", index=4)
            + _sse_message_stop()
        )
        tools, ended = _all_tools_from_sse_buf(buf)
        assert [t["tool_name"] for t in tools] == ["tool_a", "tool_b", "tool_c"]
        assert [t["index"] for t in tools] == [0, 2, 4]
        assert ended is True

    def test_text_blocks_ignored(self):
        buf = (
            _sse_text_block_start(0)
            + _sse_content_block_start("Bash", index=1)
            + _sse_text_block_start(2)
            + _sse_content_block_start("Read", index=3)
            + _sse_message_stop()
        )
        tools, ended = _all_tools_from_sse_buf(buf)
        assert [t["tool_name"] for t in tools] == ["Bash", "Read"]
        assert ended is True

    def test_no_tools_text_only(self):
        buf = _sse_message_start() + _sse_text_block_start(0) + _sse_message_stop()
        tools, ended = _all_tools_from_sse_buf(buf)
        assert tools == []
        assert ended is True

    def test_message_stop_without_tools(self):
        buf = _sse_message_stop()
        tools, ended = _all_tools_from_sse_buf(buf)
        assert tools == []
        assert ended is True

    def test_empty_buffer(self):
        tools, ended = _all_tools_from_sse_buf(b"")
        assert tools == []
        assert ended is False

    def test_tool_index_preserved(self):
        buf = _sse_content_block_start("my_tool", index=7)
        tools, ended = _all_tools_from_sse_buf(buf)
        assert tools == [{"index": 7, "tool_name": "my_tool"}]

    def test_malformed_json_skipped(self):
        malformed = b"event: content_block_start\ndata: {not valid json}\n\n"
        good = _sse_content_block_start("good_tool", index=0)
        tools, ended = _all_tools_from_sse_buf(malformed + good)
        assert [t["tool_name"] for t in tools] == ["good_tool"]

    def test_non_utf8_does_not_raise(self):
        buf = b"\xff\xfe" + _sse_content_block_start("safe_tool")
        tools, ended = _all_tools_from_sse_buf(buf)
        assert isinstance(tools, list)
        assert isinstance(ended, bool)


# ── Tests: _all_tools_from_response_body ──────────────────────────────────────


class TestAllToolsFromResponseBody:
    """TCP-IMP-21: all-tools non-streaming extraction retains second and later calls."""

    def test_single_tool(self):
        body = json.dumps(
            {"content": [{"type": "tool_use", "name": "Bash", "id": "1", "input": {}}]}
        ).encode()
        tools = _all_tools_from_response_body(body)
        assert tools == [{"index": 0, "tool_name": "Bash"}]

    def test_two_tools_both_retained(self):
        """Second tool call must not be dropped — non-streaming equivalent of TCP-IMP-21."""
        body = json.dumps(
            {
                "content": [
                    {"type": "tool_use", "name": "tool_a", "id": "1", "input": {}},
                    {"type": "tool_use", "name": "tool_b", "id": "2", "input": {}},
                ]
            }
        ).encode()
        tools = _all_tools_from_response_body(body)
        assert [t["tool_name"] for t in tools] == ["tool_a", "tool_b"]
        assert [t["index"] for t in tools] == [0, 1]

    def test_text_blocks_excluded(self):
        body = json.dumps(
            {
                "content": [
                    {"type": "text", "text": "Thinking..."},
                    {"type": "tool_use", "name": "Read", "id": "1", "input": {}},
                    {"type": "text", "text": "Also..."},
                    {"type": "tool_use", "name": "Write", "id": "2", "input": {}},
                ]
            }
        ).encode()
        tools = _all_tools_from_response_body(body)
        assert [t["tool_name"] for t in tools] == ["Read", "Write"]
        assert [t["index"] for t in tools] == [1, 3]

    def test_no_tools_returns_empty(self):
        body = json.dumps({"content": [{"type": "text", "text": "hello"}]}).encode()
        assert _all_tools_from_response_body(body) == []

    def test_empty_content_returns_empty(self):
        body = json.dumps({"content": []}).encode()
        assert _all_tools_from_response_body(body) == []

    def test_invalid_json_returns_empty(self):
        assert _all_tools_from_response_body(b"not json") == []


# ── Tests: _first_tool_from_response_body ─────────────────────────────────────


class TestFirstToolFromResponseBody:
    def test_non_streaming_tool_use(self):
        body = json.dumps(
            {
                "type": "message",
                "content": [
                    {"type": "text", "text": "Sure"},
                    {
                        "type": "tool_use",
                        "id": "toolu_x",
                        "name": "mcp__git__status",
                        "input": {},
                    },
                ],
            }
        ).encode()
        assert _first_tool_from_response_body(body) == "mcp__git__status"

    def test_non_streaming_text_only(self):
        body = json.dumps(
            {"type": "message", "content": [{"type": "text", "text": "Hello"}]}
        ).encode()
        assert _first_tool_from_response_body(body) is None

    def test_empty_content_list(self):
        body = json.dumps({"type": "message", "content": []}).encode()
        assert _first_tool_from_response_body(body) is None

    def test_invalid_json(self):
        assert _first_tool_from_response_body(b"not json") is None

    def test_multiple_tool_blocks_returns_first(self):
        body = json.dumps(
            {
                "content": [
                    {"type": "tool_use", "name": "tool_a", "id": "1", "input": {}},
                    {"type": "tool_use", "name": "tool_b", "id": "2", "input": {}},
                ]
            }
        ).encode()
        assert _first_tool_from_response_body(body) == "tool_a"


# ── Tests: _compute_expected_tool_name ────────────────────────────────────────


class TestComputeExpectedToolName:
    """TCP-IMP-22: expected_tool_name is emitted only with defensible evidence."""

    def test_single_survivor_emits_name(self):
        meta = {"survivor_count": 1, "survivor_names_sorted": ["mcp__git__status"]}
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name == "mcp__git__status"
        assert d.derivation_source == "single_survivor"
        assert d.abstain_reason is None
        assert d.candidate_set_size == 1

    def test_single_survivor_returns_derivation_type(self):
        meta = {"survivor_count": 1, "survivor_names_sorted": ["Bash"]}
        assert isinstance(_compute_expected_tool_name(meta), _ExpectedToolDerivation)

    # TCP-IMP-22: k=2 and k=3 count fallbacks removed — ambiguous, not defensible.
    def test_two_survivors_abstains(self):
        meta = {"survivor_count": 2, "survivor_names_sorted": ["tool_a", "tool_b"]}
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None
        assert d.abstain_reason == "ambiguous_2_survivors"
        assert d.candidate_set_size == 2

    def test_three_survivors_abstains(self):
        meta = {"survivor_count": 3, "survivor_names_sorted": ["alpha", "beta", "gamma"]}
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None
        assert d.abstain_reason == "ambiguous_3_survivors"

    def test_four_survivors_abstains(self):
        meta = {"survivor_count": 4, "survivor_names_sorted": ["a", "b", "c", "d"]}
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None
        assert d.abstain_reason == "ambiguous_4_survivors"

    def test_zero_survivors_abstains_no_survivors(self):
        meta = {"survivor_count": 0, "survivor_names_sorted": []}
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None
        assert d.abstain_reason == "no_survivors"

    def test_none_meta_abstains_no_meta(self):
        d = _compute_expected_tool_name(None)
        assert d.expected_tool_name is None
        assert d.abstain_reason == "no_meta"

    def test_missing_fields_abstains_no_survivors(self):
        d = _compute_expected_tool_name({})
        assert d.expected_tool_name is None
        assert d.abstain_reason == "no_survivors"

    def test_survivor_count_mismatch_abstains(self):
        # count says 1 but list is empty → defensive abstain
        meta = {"survivor_count": 1, "survivor_names_sorted": []}
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None
        assert d.abstain_reason == "count_list_mismatch"

    # TCP-IMP-22: similarity field is DIAGNOSTIC ONLY — must not drive derivation.
    def test_similarity_field_does_not_emit_expected_tool(self):
        """174 survivors + similarity hint → still abstains (no defensible evidence)."""
        meta = {
            "survivor_count": 174,
            "survivor_names_sorted": ["Bash", "Read", "Write"],
            "top_survivor_by_similarity": "Bash",
        }
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None
        assert "ambiguous" in (d.abstain_reason or "")

    def test_similarity_field_with_two_survivors_still_abstains(self):
        """Similarity hint with ambiguous gate → abstains, not emits."""
        meta = {
            "survivor_count": 2,
            "survivor_names_sorted": ["alpha", "zeta"],
            "top_survivor_by_similarity": "zeta",
        }
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None
        assert d.abstain_reason == "ambiguous_2_survivors"

    def test_no_similarity_field_two_survivors_abstains(self):
        """No similarity field, k=2: abstains (alphabetical pick is not evidence)."""
        meta = {"survivor_count": 2, "survivor_names_sorted": ["tool_a", "tool_b"]}
        d = _compute_expected_tool_name(meta)
        assert d.expected_tool_name is None


# ── Tests: _top_survivor_by_prompt_similarity ─────────────────────────────────


class TestTopSurvivorByPromptSimilarity:
    def _tool(self, name: str, description: str) -> dict:
        return {"name": name, "description": description}

    def test_returns_best_matching_tool(self):
        tools = [
            self._tool("Bash", "Execute shell commands"),
            self._tool("Read", "Read file contents from disk"),
            self._tool("mcp__git__git_status", "Show working tree git status"),
        ]
        # Prompt about reading a file → Read should win
        result = _top_survivor_by_prompt_similarity("read the file contents", tools)
        assert result == "Read"

    def test_returns_none_for_empty_tools(self):
        assert _top_survivor_by_prompt_similarity("do something", []) is None

    def test_returns_none_for_empty_prompt(self):
        tools = [self._tool("Bash", "Execute shell commands")]
        assert _top_survivor_by_prompt_similarity("", tools) is None

    def test_returns_none_for_none_prompt(self):
        tools = [self._tool("Bash", "Execute shell commands")]
        assert _top_survivor_by_prompt_similarity(None, tools) is None  # type: ignore[arg-type]

    def test_single_tool_always_wins(self):
        tools = [self._tool("OnlyTool", "Does the only thing")]
        result = _top_survivor_by_prompt_similarity("anything at all", tools)
        assert result == "OnlyTool"

    def test_git_prompt_favours_git_tool(self):
        tools = [
            self._tool("Read", "Read file from disk"),
            self._tool("mcp__git__git_status", "Show git working tree status"),
            self._tool("Bash", "Run shell commands"),
        ]
        result = _top_survivor_by_prompt_similarity(
            "show git status of working tree", tools
        )
        assert result == "mcp__git__git_status"


# ── Tests: first_tool_correct computation ─────────────────────────────────────


class TestFirstToolCorrect:
    """first_tool_correct is computed inside _write_decision_record.
    Test by reading what was written to decisions.jsonl."""

    def _read_last_record(self, path: Path) -> dict:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return json.loads(lines[-1])

    def test_correct_when_names_match(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 1, "survivor_names_sorted": ["Bash"]}
        _write_decision_record(time.time(), meta, "Bash")
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is True

    def test_incorrect_when_names_differ(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 1, "survivor_names_sorted": ["Bash"]}
        _write_decision_record(time.time(), meta, "mcp__git__status")
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is False

    def test_none_when_first_tool_name_is_null(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 1, "survivor_names_sorted": ["Bash"]}
        _write_decision_record(time.time(), meta, None)
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is None

    def test_none_when_expected_is_null(self, tmp_path, monkeypatch):
        # TCP-IMP-22: ambiguous gate (count >= 2) abstains → expected_tool_name is None.
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 4, "survivor_names_sorted": ["a", "b", "c", "d"]}
        _write_decision_record(time.time(), meta, "a")
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is None
        assert rec["expected_tool_name"] is None
        assert rec["expected_tool_abstain_reason"] == "ambiguous_4_survivors"


# ── Tests: backward compatibility ─────────────────────────────────────────────


class TestBackwardCompatibility:
    """New fields must be present and typed correctly so existing readers
    can use .get() without crashing."""

    def _read_last_record(self, path: Path) -> dict:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return json.loads(lines[-1])

    def test_new_fields_always_present(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 1, "survivor_names_sorted": ["Bash"]}
        _write_decision_record(time.time(), meta, "Bash")
        rec = self._read_last_record(log)
        assert "first_tool_name" in rec
        assert "expected_tool_name" in rec
        assert "first_tool_correct" in rec
        assert "preflight_duration_ms" in rec
        assert "upstream_request_duration_ms" in rec
        assert "first_byte_duration_ms" in rec
        assert "total_response_duration_ms" in rec
        assert "retry_count" in rec
        # TCP-IMP-21: tool_call_sequence must always be present (None when not supplied)
        assert "tool_call_sequence" in rec
        # TCP-IMP-22: derivation diagnostic fields
        assert "expected_tool_derivation_source" in rec
        assert "expected_tool_candidate_set_size" in rec
        assert "expected_tool_abstain_reason" in rec

    def test_tool_call_sequence_null_when_not_supplied(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 1, "survivor_names_sorted": ["Bash"]}
        _write_decision_record(time.time(), meta, "Bash")
        rec = self._read_last_record(log)
        assert rec["tool_call_sequence"] is None

    def test_tool_call_sequence_multi_tool_retained(self, tmp_path, monkeypatch):
        """TCP-IMP-21: second and later tool calls must survive the decision record."""
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 2, "survivor_names_sorted": ["Read", "Write"]}
        seq = [
            {"seq": 0, "index": 0, "tool_name": "Read"},
            {"seq": 1, "index": 1, "tool_name": "Write"},
        ]
        _write_decision_record(time.time(), meta, "Read", tool_call_sequence=seq)
        rec = self._read_last_record(log)
        assert rec["first_tool_name"] == "Read"
        assert rec["tool_call_sequence"] == seq
        # Second tool call is present — it would previously have been lost.
        assert rec["tool_call_sequence"][1]["tool_name"] == "Write"

    def test_record_is_valid_json(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"mode": "shadow", "survivor_count": 0, "survivor_names_sorted": []}
        _write_decision_record(time.time(), meta, None)
        line = log.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert isinstance(parsed, dict)

    def test_ts_field_is_numeric(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        ts = time.time()
        meta = {"survivor_count": 0, "survivor_names_sorted": []}
        _write_decision_record(ts, meta, None)
        rec = self._read_last_record(log)
        assert isinstance(rec["ts"], float)
        assert abs(rec["ts"] - ts) < 1.0

    def test_single_survivor_derivation_fields_in_record(self, tmp_path, monkeypatch):
        """TCP-IMP-22: single-survivor turns write derivation_source and no abstain."""
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 1, "survivor_names_sorted": ["Read"]}
        _write_decision_record(time.time(), meta, "Read")
        rec = self._read_last_record(log)
        assert rec["expected_tool_name"] == "Read"
        assert rec["expected_tool_derivation_source"] == "single_survivor"
        assert rec["expected_tool_candidate_set_size"] == 1
        assert rec["expected_tool_abstain_reason"] is None
        assert rec["first_tool_correct"] is True

    def test_abstained_turn_writes_reason_to_record(self, tmp_path, monkeypatch):
        """TCP-IMP-22: ambiguous turns write abstain_reason, no expected_tool_name."""
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 5, "survivor_names_sorted": ["a", "b", "c", "d", "e"]}
        _write_decision_record(time.time(), meta, "a")
        rec = self._read_last_record(log)
        assert rec["expected_tool_name"] is None
        assert rec["expected_tool_abstain_reason"] == "ambiguous_5_survivors"
        assert rec["expected_tool_derivation_source"] is None
        assert rec["first_tool_correct"] is None

    def test_similarity_field_preserved_as_diagnostic(self, tmp_path, monkeypatch):
        """TCP-IMP-22: top_survivor_by_similarity is preserved in record but not used."""
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {
            "survivor_count": 10,
            "survivor_names_sorted": ["Bash", "Read"],
            "top_survivor_by_similarity": "Bash",
        }
        _write_decision_record(time.time(), meta, "Bash")
        rec = self._read_last_record(log)
        # Similarity hint present in record (diagnostic field preserved)
        assert rec.get("top_survivor_by_similarity") == "Bash"
        # But expected_tool_name was NOT emitted from it
        assert rec["expected_tool_name"] is None
        assert "ambiguous" in rec["expected_tool_abstain_reason"]


# ── Tests: latency guard ───────────────────────────────────────────────────────


class TestResponseTapLatency:
    """The SSE tap must add negligible overhead on each chunk inspection."""

    def test_sse_buf_scan_latency(self):
        """Scanning a realistic 4 KB SSE buffer must complete in under 1 ms."""
        # Build a realistic-sized buffer: message_start + text delta * N + message_stop
        chunks = [_sse_message_start()]
        for i in range(20):
            data = json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "word " * 10},
                }
            )
            chunks.append(f"event: content_block_delta\ndata: {data}\n\n".encode())
        chunks.append(_sse_message_stop())
        buf = b"".join(chunks)

        N = 1000
        start = time.perf_counter()
        for _ in range(N):
            _first_tool_from_sse_buf(buf)
        elapsed_ms = (time.perf_counter() - start) * 1000 / N
        # Each scan must be under 1 ms on any reasonable hardware.
        assert elapsed_ms < 1.0, f"SSE scan took {elapsed_ms:.3f} ms per call"

    def test_response_body_parse_latency(self):
        """Parsing a realistic 2 KB non-streaming response must be under 1 ms."""
        body = json.dumps(
            {
                "type": "message",
                "content": [
                    {"type": "text", "text": "I'll help you with that. " * 20},
                    {
                        "type": "tool_use",
                        "id": "toolu_x",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                ],
            }
        ).encode()

        N = 1000
        start = time.perf_counter()
        for _ in range(N):
            _first_tool_from_response_body(body)
        elapsed_ms = (time.perf_counter() - start) * 1000 / N
        assert elapsed_ms < 1.0, f"Response parse took {elapsed_ms:.3f} ms per call"


# ── Tests: denial enforcement integration ─────────────────────────────────────


class TestDenialEnforcementIntegration:
    """Phase 2A: _check_denial_enforcement wired into the proxy response tap.

    Tests verify the flat denial_* fields written to the decisions.jsonl row.
    """

    def _make_crg_records(self, status: str) -> list[dict]:
        """Build a crg_resolutions list with one resolution of the given status."""
        from dataclasses import replace

        from tcp.proxy.capability_resolution_gate import (
            CRGContext,
            resolve_capability,
            resolution_to_log_record,
        )

        _NOTION_TOOL = "mcp__notion-agents__query_database"
        if status == "schema_deferred":
            ctx = CRGContext(
                visible_tools=frozenset(),
                deferred_tools=frozenset(),
                latent_tools=frozenset({_NOTION_TOOL}),
                connector_servers=frozenset(),
                policy_blocked_tools=frozenset(),
                mode="live",
            )
        elif status == "unavailable":
            ctx = CRGContext(
                visible_tools=frozenset(),
                deferred_tools=frozenset(),
                latent_tools=frozenset(),
                connector_servers=frozenset(),
                policy_blocked_tools=frozenset(),
                mode="live",
            )
        else:
            raise ValueError(f"unsupported status for helper: {status}")

        r = resolve_capability("notion.search", ctx)
        assert r.status == status
        return [resolution_to_log_record(r)]

    def test_streaming_schema_deferred_triggers_violation(self):
        """1. Streaming: absence-language + schema_deferred → denial_violation=True."""
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        meta: dict = {"crg_resolutions": self._make_crg_records("schema_deferred")}
        _check_denial_enforcement("I don't have access to Notion.", meta)

        assert meta["denial_violation"] is True
        assert meta["denial_rewrite_action"] == "surface_schema_deferred_tool"
        assert meta["denial_matched_phrase"] is not None
        assert meta["denial_violation_reason"] is not None

    def test_non_streaming_unavailable_all_surfaces_no_violation(self):
        """2. Non-streaming: absence-language + valid unavailable → denial_violation=False."""
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        meta: dict = {"crg_resolutions": self._make_crg_records("unavailable")}
        _check_denial_enforcement("No tool is available for Notion.", meta)

        assert meta["denial_violation"] is False
        assert meta["denial_violation_reason"] is None
        assert meta["denial_rewrite_action"] is None

    def test_tool_use_response_no_absence_no_flat_fields(self):
        """3. Tool-use response with no absence-language → no denial_violation field set."""
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        meta: dict = {"crg_resolutions": []}
        # Pass text with no absence-language (empty text also qualifies).
        _check_denial_enforcement("", meta)

        assert "denial_violation" not in meta

    def test_backward_compat_fields_preserved(self, tmp_path, monkeypatch):
        """4. Existing decision-record fields remain intact after denial gate runs."""
        import time

        from tcp.proxy.cc_proxy import _check_denial_enforcement

        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)

        crg_records = self._make_crg_records("schema_deferred")
        meta: dict = {
            "survivor_count": 1,
            "survivor_names_sorted": ["Bash"],
            "crg_resolutions": crg_records,
            "crg_resolution_count": 1,
            "crg_false_denial_risk": True,
        }
        # Simulate denial enforcement as the streaming tap would.
        _check_denial_enforcement("I don't have access to Notion.", meta)
        seq = [{"seq": 0, "index": 0, "tool_name": "Bash"}]
        _write_decision_record(time.time(), meta, "Bash", tool_call_sequence=seq)

        rec = json.loads(log.read_text(encoding="utf-8").strip())

        # Existing fields still present.
        assert rec["first_tool_name"] == "Bash"
        assert rec["tool_call_sequence"] == seq
        assert "crg_resolutions" in rec
        assert "crg_resolution_count" in rec
        assert "crg_false_denial_risk" in rec

        # New flat denial fields also present.
        assert "denial_violation" in rec
        assert isinstance(rec["denial_violation"], bool)
        assert "denial_resolution_statuses" in rec
        assert isinstance(rec["denial_resolution_statuses"], list)

    def test_schema_deferred_absence_writes_violation_true_to_jsonl(
        self, tmp_path, monkeypatch
    ):
        """Streaming path: schema_deferred + absence-language → denial_violation=True in JSONL."""
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)

        crg_records = self._make_crg_records("schema_deferred")
        meta: dict = {
            "survivor_count": 0,
            "survivor_names_sorted": [],
            "crg_resolutions": crg_records,
        }
        _check_denial_enforcement("I don't have access to Notion.", meta)
        _write_decision_record(time.time(), meta, None)

        rec = json.loads(log.read_text(encoding="utf-8").strip())
        assert rec["denial_violation"] is True
        assert rec["denial_rewrite_action"] == "surface_schema_deferred_tool"
        assert rec["denial_matched_phrase"] is not None

    def test_unavailable_absence_writes_violation_false_to_jsonl(
        self, tmp_path, monkeypatch
    ):
        """Non-streaming path: valid unavailable + absence-language → denial_violation=False in JSONL."""
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)

        crg_records = self._make_crg_records("unavailable")
        meta: dict = {
            "survivor_count": 0,
            "survivor_names_sorted": [],
            "crg_resolutions": crg_records,
        }
        _check_denial_enforcement("No tool is available for Notion.", meta)
        _write_decision_record(time.time(), meta, None)

        rec = json.loads(log.read_text(encoding="utf-8").strip())
        assert rec["denial_violation"] is False
        assert rec["denial_rewrite_action"] is None
        assert rec["denial_violation_reason"] is None

    def test_no_absence_language_denial_fields_absent_from_jsonl(
        self, tmp_path, monkeypatch
    ):
        """Tool-use response with no absence-language → denial flat fields not written."""
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)

        meta: dict = {"survivor_count": 1, "survivor_names_sorted": ["Bash"], "crg_resolutions": []}
        _check_denial_enforcement("", meta)
        _write_decision_record(time.time(), meta, "Bash")

        rec = json.loads(log.read_text(encoding="utf-8").strip())
        # _check_denial_enforcement only writes denial_* when absence-language is found.
        assert "denial_violation" not in rec
