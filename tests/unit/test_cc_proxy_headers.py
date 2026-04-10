"""Upstream header forwarding must not pin stale Content-Length."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
from starlette.requests import Request

from tcp.proxy.cc_proxy import (
    _buffered_response_headers,
    _forward_headers,
    _streaming_response_headers,
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
