"""Tests for mock tool executors."""

from __future__ import annotations

import json

import pytest

from tcp.agent.mock_executors import MOCK_RESPONSES, get_mock_executor


class TestMockResponses:
    """Verify canned responses are valid JSON."""

    def test_all_responses_are_valid_json(self):
        for tool_name, response in MOCK_RESPONSES.items():
            parsed = json.loads(response)
            assert isinstance(parsed, dict), f"{tool_name} response is not a dict"

    def test_expected_tools_present(self):
        """Tools referenced by MT-3 tasks must have canned responses."""
        expected = {
            "fs-read-file",
            "jq",
            "git-status",
            "fs-search-files",
            "rag-query-documents",
            "git-commit",
            "chmod",
            "systemctl",
            "web-fetch",
            "oracle-execute-query",
        }
        missing = expected - set(MOCK_RESPONSES.keys())
        assert not missing, f"Missing mock responses for: {missing}"

    def test_full_corpus_coverage(self):
        """Every tool in the MT-3 corpus should have a mock response."""
        from tcp.harness.corpus import build_mcp_corpus

        entries = build_mcp_corpus()
        corpus_names = {e.descriptor.name for e in entries}
        missing = corpus_names - set(MOCK_RESPONSES.keys())
        assert not missing, f"Missing mock responses for corpus tools: {missing}"


class TestGetMockExecutor:
    """Verify the executor callable."""

    def test_returns_callable(self):
        executor = get_mock_executor()
        assert callable(executor)

    def test_known_tool_returns_canned_response(self):
        executor = get_mock_executor()
        result = executor("fs-read-file", {"input": "/tmp/test"})
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_unknown_tool_returns_default(self):
        executor = get_mock_executor()
        result = executor("nonexistent-tool-xyz", {})
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_response_is_string(self):
        executor = get_mock_executor()
        result = executor("jq", {"input": "test"})
        assert isinstance(result, str)
