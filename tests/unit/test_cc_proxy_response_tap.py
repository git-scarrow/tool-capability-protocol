"""Tests for TCP-IMP-14: response-tap helpers in cc_proxy.

Covers:
 - SSE first-tool extraction (normal, cross-chunk-boundary, no-tool)
 - Non-streamed JSON extraction
 - expected_tool_name derivation
 - first_tool_correct computation
 - decisions.jsonl enrichment (one record per turn)
 - backward compatibility (new fields present with correct types)
 - latency guard (tap overhead is negligible)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tcp.proxy.cc_proxy import (
    _compute_expected_tool_name,
    _derive_expected_tool_from_survivors,
    _first_tool_from_response_body,
    _first_tool_from_sse_buf,
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
    # TCP-IMP-18: _compute_expected_tool_name reads top_survivor_by_similarity
    # (produced by _derive_expected_tool_from_survivors). Count-based alphabetical
    # fallback removed — meta without the field returns None.

    def test_reads_top_survivor_field(self):
        meta = {"top_survivor_by_similarity": "mcp__git__status"}
        assert _compute_expected_tool_name(meta) == "mcp__git__status"

    def test_returns_none_when_field_absent(self):
        # TCP-IMP-18: no count-based fallback; absent field → None
        meta = {"survivor_count": 1, "survivor_names_sorted": ["mcp__git__status"]}
        assert _compute_expected_tool_name(meta) is None

    def test_returns_none_for_multi_survivor_without_field(self):
        meta = {"survivor_count": 2, "survivor_names_sorted": ["tool_a", "tool_b"]}
        assert _compute_expected_tool_name(meta) is None

    def test_returns_none_for_four_survivors_without_field(self):
        meta = {"survivor_count": 4, "survivor_names_sorted": ["a", "b", "c", "d"]}
        assert _compute_expected_tool_name(meta) is None

    def test_returns_none_for_zero_survivors_without_field(self):
        meta = {"survivor_count": 0, "survivor_names_sorted": []}
        assert _compute_expected_tool_name(meta) is None

    def test_none_meta_returns_none(self):
        assert _compute_expected_tool_name(None) is None

    def test_missing_fields_returns_none(self):
        assert _compute_expected_tool_name({}) is None

    def test_top_survivor_field_present(self):
        meta = {
            "survivor_count": 174,
            "survivor_names_sorted": ["Bash", "Read", "Write"],
            "top_survivor_by_similarity": "Bash",
        }
        assert _compute_expected_tool_name(meta) == "Bash"

    def test_top_survivor_field_takes_non_alphabetical_value(self):
        meta = {
            "survivor_count": 2,
            "survivor_names_sorted": ["alpha", "zeta"],
            "top_survivor_by_similarity": "zeta",
        }
        assert _compute_expected_tool_name(meta) == "zeta"


# ── Tests: _derive_expected_tool_from_survivors ────────────────────────────────


class TestDeriveExpectedToolFromSurvivors:
    # TCP-IMP-18: deterministic single-survivor path
    def test_single_survivor_always_returned(self):
        assert (
            _derive_expected_tool_from_survivors("anything at all", ["OnlyTool"])
            == "OnlyTool"
        )

    def test_single_survivor_no_text_still_returned(self):
        assert _derive_expected_tool_from_survivors("", ["Read"]) == "Read"

    def test_single_survivor_none_text_still_returned(self):
        assert _derive_expected_tool_from_survivors(None, ["Read"]) == "Read"

    def test_empty_survivors_returns_none(self):
        assert _derive_expected_tool_from_survivors("do something", []) is None

    # TCP-IMP-18: name-only matching (no description text)
    def test_two_survivors_name_match(self):
        # "read" appears in prompt and matches "Read" name
        result = _derive_expected_tool_from_survivors(
            "read the file contents", ["Bash", "Read"]
        )
        assert result == "Read"

    def test_git_name_match(self):
        result = _derive_expected_tool_from_survivors(
            "show git status of working tree",
            ["Bash", "mcp__git__git_status", "Read"],
        )
        assert result == "mcp__git__git_status"

    def test_above_threshold_returns_none(self):
        # 4 survivors > k=3 → always None regardless of prompt
        result = _derive_expected_tool_from_survivors(
            "use Bash to run something", ["Bash", "Read", "Write", "Edit"]
        )
        assert result is None

    def test_empty_prompt_with_two_survivors_returns_none(self):
        assert _derive_expected_tool_from_survivors("", ["Bash", "Read"]) is None

    def test_none_prompt_with_two_survivors_returns_none(self):
        assert _derive_expected_tool_from_survivors(None, ["Bash", "Read"]) is None

    # TCP-IMP-18: prompt-excerpt lexical collision guard
    # The old SequenceMatcher-on-description approach would rank a tool highly if its
    # description repeated the user's task phrase. Name-only matching is immune.
    def test_description_excerpt_in_prompt_does_not_inflate_wrong_tool(self):
        # Prompt echoes exactly what the "Write" tool description might say,
        # but the user's last message says "read".
        # With name-only matching, "Read" wins because "read" is in the prompt.
        result = _derive_expected_tool_from_survivors(
            "read the configuration value",
            ["Read", "Write"],
        )
        assert result == "Read"


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
        # TCP-IMP-18: expected_tool_name comes from top_survivor_by_similarity
        meta = {
            "survivor_count": 1,
            "survivor_names_sorted": ["Bash"],
            "top_survivor_by_similarity": "Bash",
        }
        _write_decision_record(time.time(), meta, "Bash")
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is True

    def test_incorrect_when_names_differ(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {
            "survivor_count": 1,
            "survivor_names_sorted": ["Bash"],
            "top_survivor_by_similarity": "Bash",
        }
        _write_decision_record(time.time(), meta, "mcp__git__status")
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is False

    def test_none_when_first_tool_name_is_null(self, tmp_path, monkeypatch):
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {
            "survivor_count": 1,
            "survivor_names_sorted": ["Bash"],
            "top_survivor_by_similarity": "Bash",
        }
        _write_decision_record(time.time(), meta, None)
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is None

    def test_none_when_expected_is_null(self, tmp_path, monkeypatch):
        # TCP-IMP-16: threshold is k=3; survivor_count > 3 means no expectation.
        log = tmp_path / "decisions.jsonl"
        monkeypatch.setattr("tcp.proxy.cc_proxy.DECISIONS_LOG", log)
        meta = {"survivor_count": 4, "survivor_names_sorted": ["a", "b", "c", "d"]}
        _write_decision_record(time.time(), meta, "a")
        rec = self._read_last_record(log)
        assert rec["first_tool_correct"] is None
        assert rec["expected_tool_name"] is None


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
