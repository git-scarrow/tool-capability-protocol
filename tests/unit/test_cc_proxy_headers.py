"""Upstream header forwarding must not pin stale Content-Length.

Also covers:
- Accept-Encoding: identity override (TCP-IMP-15)
- tap_skipped field presence and correctness in decision records (TCP-IMP-15)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from tcp.proxy.cc_proxy import (
    _buffered_response_headers,
    _forward_headers,
    _streaming_response_headers,
    _write_decision_record,
    build_app,
)


def test_forward_headers_omit_content_length() -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "path": "/v1/messages",
        "raw_path": b"/v1/messages",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", b"9999"),
            (b"x-api-key", b"sk-test"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8742),
        "scheme": "http",
        "root_path": "",
    }

    async def empty_receive() -> dict:
        return {"type": "http.disconnect"}

    req = Request(scope, empty_receive)
    hdrs = _forward_headers(req)
    assert "content-length" not in {k.lower() for k in hdrs}
    assert hdrs.get("x-api-key") == "sk-test"


def test_streaming_response_headers_drop_length_keep_encoding() -> None:
    resp = MagicMock()
    resp.headers = httpx.Headers(
        {
            "content-type": "text/event-stream",
            "content-encoding": "gzip",
            "content-length": "12345",
        }
    )
    hdrs = _streaming_response_headers(resp)
    assert hdrs.get("content-encoding") == "gzip"
    assert "content-length" not in {k.lower() for k in hdrs}


def test_buffered_response_headers_strip_encoding_and_fix_length() -> None:
    resp = MagicMock()
    resp.headers = httpx.Headers(
        {
            "content-type": "application/json",
            "content-encoding": "gzip",
            "content-length": "999",
        }
    )
    body = b'{"ok":true}'
    hdrs = _buffered_response_headers(resp, body)
    assert "content-encoding" not in {k.lower() for k in hdrs}
    assert hdrs.get("content-length") == str(len(body))


def test_catch_all_proxy_route_accepts_post() -> None:
    app = build_app()
    catch_all = next(route for route in app.routes if getattr(route, "path", None) == "/{path:path}")
    assert "POST" in catch_all.methods


# ── TCP-IMP-15: Accept-Encoding override ──────────────────────────────────────

def test_proxy_sends_exactly_one_accept_encoding_identity() -> None:
    """proxy_post_messages must send exactly Accept-Encoding: identity upstream,
    even when the client supplied gzip/br — no duplicate headers."""
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        body = json.dumps({"id": "msg_1", "type": "message", "role": "assistant",
                           "content": [], "model": "claude-3-5-sonnet-20241022",
                           "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": {"input_tokens": 1, "output_tokens": 1}})
        return httpx.Response(200, content=body.encode(),
                              headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    _real_AsyncClient = httpx.AsyncClient

    def _patched_client(**kw: object) -> httpx.AsyncClient:
        kw.pop("transport", None)
        return _real_AsyncClient(transport=transport, **kw)  # type: ignore[arg-type]

    with patch("tcp.proxy.cc_proxy.httpx.AsyncClient", _patched_client):
        with patch("tcp.proxy.cc_proxy._read_mode", return_value="shadow"):
            app = build_app()
            client = TestClient(app)
            payload = json.dumps({"model": "claude-3-5-sonnet-20241022",
                                  "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]})
            client.post(
                "/v1/messages",
                content=payload,
                headers={"content-type": "application/json",
                         "x-api-key": "sk-test",
                         "accept-encoding": "gzip, br"},
            )

    assert len(captured) == 1, "expected exactly one upstream request"
    req = captured[0]
    ae_values = [v for k, v in req.headers.items() if k.lower() == "accept-encoding"]
    assert ae_values == ["identity"], (
        f"expected exactly ['identity'] but got {ae_values}"
    )


# ── TCP-IMP-15: tap_skipped field presence ────────────────────────────────────

def _make_meta() -> dict:
    return {
        "mode": "shadow",
        "survivor_names_sorted": [],
        "suppressed_names_sorted": [],
        "total_tools_before": 0,
        "total_tools_after": 0,
        "description_similarity_max": None,
        "task_prompt_hash": None,
        "session_start_event": None,
        "derived_intent": None,
        "derivation_method": None,
    }


def test_decision_record_tap_skipped_field_always_present() -> None:
    """Every decision record must include a tap_skipped boolean key."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "decisions.jsonl"
        with patch("tcp.proxy.cc_proxy.DECISIONS_LOG", log_path):
            _write_decision_record(1.0, _make_meta(), None, tap_skipped=False)
        record = json.loads(log_path.read_text())
        assert "tap_skipped" in record
        assert isinstance(record["tap_skipped"], bool)


def test_decision_record_tap_skipped_true_when_can_tap_false() -> None:
    """tap_skipped must be True when can_tap is False (compressed response)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "decisions.jsonl"
        with patch("tcp.proxy.cc_proxy.DECISIONS_LOG", log_path):
            _write_decision_record(1.0, _make_meta(), None, tap_skipped=True)
        record = json.loads(log_path.read_text())
        assert record["tap_skipped"] is True


def test_decision_record_tap_skipped_false_when_can_tap_true() -> None:
    """tap_skipped must be False when can_tap is True (uncompressed response)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "decisions.jsonl"
        with patch("tcp.proxy.cc_proxy.DECISIONS_LOG", log_path):
            _write_decision_record(1.0, _make_meta(), "bash", tap_skipped=False)
        record = json.loads(log_path.read_text())
        assert record["tap_skipped"] is False
