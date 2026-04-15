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
            "content_block": {"type": "tool_use", "id": "toolu_abc", "name": name, "input": {}},
        }
    )
    return f"event: content_block_start\ndata: {data}\n\n".encode()


def _sse_text_block_start(index: int = 0) -> bytes:
    data = json.dumps(
        {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}
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
        buf = _sse_text_block_start(0) + _sse_content_block_start("mcp__fs__read_file", index=1)
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
                    {"type": "tool_use", "id": "toolu_x", "name": "mcp__git__status", "input": {}},
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
    def test_single_survivor(self):
        meta = {"survivor_count": 1, "survivor_names_sorted": ["mcp__git__status"]}
        assert _compute_expected_tool_name(meta) == "mcp__git__status"

    # TCP-IMP-16: loosen threshold to k=3 — 2 survivors now emits first survivor
    def test_two_survivors_returns_first_survivor(self):
        meta = {"survivor_count": 2, "survivor_names_sorted": ["tool_a", "tool_b"]}
        assert _compute_expected_tool_name(meta) == "tool_a"

    def test_three_survivors_returns_first_survivor(self):
        meta = {"survivor_count": 3, "survivor_names_sorted": ["alpha", "beta", "gamma"]}
        assert _compute_expected_tool_name(meta) == "alpha"

    def test_four_survivors_returns_none(self):
        meta = {"survivor_count": 4, "survivor_names_sorted": ["a", "b", "c", "d"]}
        assert _compute_expected_tool_name(meta) is None

    def test_zero_survivors_returns_none(self):
        meta = {"survivor_count": 0, "survivor_names_sorted": []}
        assert _compute_expected_tool_name(meta) is None

    def test_none_meta_returns_none(self):
        assert _compute_expected_tool_name(None) is None

    def test_missing_fields_returns_none(self):
        assert _compute_expected_tool_name({}) is None

    def test_survivor_count_mismatch_with_list(self):
        # count says 1 but list is empty → return None (defensive)
        meta = {"survivor_count": 1, "survivor_names_sorted": []}
        assert _compute_expected_tool_name(meta) is None

    def test_count_mismatch_above_k_returns_none(self):
        # count says 2 but list has 4 entries → trust count, return None
        meta = {"survivor_count": 4, "survivor_names_sorted": ["a", "b", "c", "d"]}
        assert _compute_expected_tool_name(meta) is None

    # TCP-IMP-17: top_survivor_by_similarity overrides count-based logic
    def test_top_survivor_by_similarity_used_when_present(self):
        """With 174 survivors, expected_tool_name comes from similarity ranking."""
        meta = {
            "survivor_count": 174,
            "survivor_names_sorted": ["Bash", "Read", "Write"],
            "top_survivor_by_similarity": "Bash",
        }
        assert _compute_expected_tool_name(meta) == "Bash"

    def test_top_survivor_by_similarity_overrides_count_k(self):
        """similarity field takes precedence over k=3 alphabetical pick."""
        meta = {
            "survivor_count": 2,
            "survivor_names_sorted": ["alpha", "zeta"],
            "top_survivor_by_similarity": "zeta",
        }
        assert _compute_expected_tool_name(meta) == "zeta"

    def test_falls_back_to_count_logic_when_similarity_absent(self):
        """No top_survivor_by_similarity → fall back to k=3 count logic."""
        meta = {"survivor_count": 2, "survivor_names_sorted": ["tool_a", "tool_b"]}
        assert _compute_expected_tool_name(meta) == "tool_a"


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
        result = _top_survivor_by_prompt_similarity("show git status of working tree", tools)
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
            data = json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "word " * 10}})
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
                    {"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {"command": "ls"}},
                ],
            }
        ).encode()

        N = 1000
        start = time.perf_counter()
        for _ in range(N):
            _first_tool_from_response_body(body)
        elapsed_ms = (time.perf_counter() - start) * 1000 / N
        assert elapsed_ms < 1.0, f"Response parse took {elapsed_ms:.3f} ms per call"
