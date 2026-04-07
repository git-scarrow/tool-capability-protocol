"""HTTP proxy: Claude Code → Anthropic with TCP gating (shadow or live)."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from tcp.derivation.request_derivation import SessionStartEvent, derive_request
from tcp.harness.gating import RuntimeEnvironment, gate_tools
from tcp.harness.models import ToolSelectionRequest
from tcp.proxy.projection import ProjectionTier, project_single_anthropic_tool
from tcp.proxy.prompt_select import extract_task_prompt

PROXY_STATE_DIR = Path.home() / ".tcp-shadow" / "proxy"
MODE_PATH = PROXY_STATE_DIR / "mode"
DECISIONS_LOG = PROXY_STATE_DIR / "decisions.jsonl"

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


def _read_mode() -> str:
    if MODE_PATH.exists():
        raw = MODE_PATH.read_text(encoding="utf-8").strip().lower()
        if raw in ("shadow", "live"):
            return raw
    env = os.environ.get("TCP_CC_PROXY_MODE", "shadow").strip().lower()
    return env if env in ("shadow", "live") else "shadow"


def _write_mode(mode: str) -> None:
    PROXY_STATE_DIR.mkdir(parents=True, exist_ok=True)
    MODE_PATH.write_text(mode + "\n", encoding="utf-8")


def _session_from_env() -> SessionStartEvent:
    return SessionStartEvent(
        session_id="tcp_cc_proxy",
        permission_mode=os.environ.get("TCP_PROXY_PERMISSION_MODE", "default"),
        cwd=os.environ.get("TCP_PROXY_CWD", os.getcwd()),
    )


def _runtime_from_env() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        network_enabled=os.environ.get("TCP_PROXY_NETWORK", "").lower()
        in ("1", "true", "yes"),
        file_access_enabled=os.environ.get("TCP_PROXY_FILE_ACCESS", "true").lower()
        not in ("0", "false", "no"),
    )


def _tool_name(tool: Mapping[str, Any]) -> str:
    n = tool.get("name")
    return str(n) if n is not None else ""


def _gate_request_for_prompt(prompt: str, session: SessionStartEvent) -> ToolSelectionRequest:
    """Capability flags only — avoids false rejects when ToolRecords lack format metadata."""
    full = derive_request(prompt, session)
    return ToolSelectionRequest.from_kwargs(
        required_capability_flags=full.required_capability_flags,
        require_auto_approval=full.require_auto_approval,
    )


def _process_tools_array(
    tools: list[Any],
    body: Mapping[str, Any],
    mode: str,
) -> tuple[list[Any], dict[str, Any]]:
    """Run projection + gate_tools; in live mode return filtered tools list."""
    messages = body.get("messages")
    prompt = extract_task_prompt(messages if isinstance(messages, list) else None)
    session = _session_from_env()
    tsel = _gate_request_for_prompt(prompt, session)
    env = _runtime_from_env()

    entries: list[tuple[Any, Any, Any, Any]] = []
    records = []
    tiers = []
    for t in tools:
        if not isinstance(t, Mapping):
            entries.append((t, None, None, None))
            continue
        rec, tier = project_single_anthropic_tool(t)
        records.append(rec)
        tiers.append(tier)
        entries.append((t, rec, tier, rec.tool_name))

    gate = gate_tools(records, tsel, env) if records else None
    survivors: set[str] = set()
    if gate:
        survivors = {x.tool_name for x in gate.approved_tools} | {
            x.tool_name for x in gate.approval_required_tools
        }

    live_tools: list[Any] = []
    for item in entries:
        orig, rec, tier, _name = item
        if rec is None:
            live_tools.append(orig)
            continue
        if tier == ProjectionTier.FALLBACK or rec.tool_name in survivors:
            live_tools.append(orig)

    audit_serial = []
    if gate:
        for a in gate.audit_log:
            audit_serial.append(
                {
                    "tool_name": a.tool_name,
                    "decision": a.decision.value,
                    "reason": a.reason,
                    "details": dict(a.details),
                }
            )

    meta: dict[str, Any] = {
        "mode": mode,
        "prompt_excerpt": prompt[:240],
        "required_capability_flags": tsel.required_capability_flags,
        "tool_count_before": len(tools),
        "tool_count_after": len(live_tools) if mode == "live" else len(tools),
        "survivor_names_sorted": sorted(survivors),
        "projection_tiers": [
            tier.name for (_o, _r, tier, _n) in entries if tier is not None
        ],
        "audit": audit_serial,
    }

    if mode == "live" and len(tools) > 0 and len(live_tools) == 0:
        meta["live_empty_fallback"] = True
        meta["tool_count_after"] = len(tools)
        return list(tools), meta

    if mode == "live":
        return live_tools, meta
    return list(tools), meta


def _maybe_transform_messages_body(raw: bytes, mode: str) -> tuple[bytes, dict[str, Any] | None]:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if not isinstance(body, dict):
        return raw, None
    tools = body.get("tools")
    if not isinstance(tools, list):
        return raw, None

    new_tools, meta = _process_tools_array(tools, body, mode)
    if mode == "shadow":
        return raw, meta

    out = dict(body)
    out["tools"] = new_tools
    return json.dumps(out).encode("utf-8"), meta


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _forward_headers(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        lk = key.lower()
        if lk in HOP_BY_HOP or lk == "host":
            continue
        out[key] = value
    return out


def _response_headers_from_httpx(response: httpx.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in response.headers.items():
        lk = key.lower()
        if lk in HOP_BY_HOP:
            continue
        out[key] = value
    return out


def _upstream_base() -> str:
    return os.environ.get("ANTHROPIC_UPSTREAM_BASE", "https://api.anthropic.com").rstrip("/")


async def proxy_post_messages(request: Request) -> Response:
    mode = _read_mode()
    raw = await request.body()
    transformed, meta = _maybe_transform_messages_body(raw, mode)
    if meta is not None:
        _append_jsonl(
            DECISIONS_LOG,
            {"ts": time.time(), "path": "/v1/messages", **meta},
        )

    url = f"{_upstream_base()}/v1/messages"
    headers = _forward_headers(request)
    stream = False
    try:
        parsed = json.loads(transformed)
        stream = bool(parsed.get("stream")) if isinstance(parsed, dict) else False
    except json.JSONDecodeError:
        stream = False

    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
    try:
        req = client.build_request(
            "POST",
            url,
            headers=headers,
            content=transformed,
        )
        response = await client.send(req, stream=stream)
    except Exception:
        await client.aclose()
        raise

    if stream:

        async def body_iter() -> Any:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            body_iter(),
            status_code=response.status_code,
            headers=_response_headers_from_httpx(response),
            media_type=response.headers.get("content-type"),
        )

    try:
        content = await response.aread()
        hdrs = _response_headers_from_httpx(response)
        return Response(
            content=content,
            status_code=response.status_code,
            headers=hdrs,
        )
    finally:
        await response.aclose()
        await client.aclose()


async def proxy_pass_through(request: Request) -> Response:
    """Forward non-/v1/messages requests unchanged (same verb, path, body)."""
    raw = await request.body()
    url = f"{_upstream_base()}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = _forward_headers(request)
    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
    try:
        req = client.build_request(request.method, url, headers=headers, content=raw)
        response = await client.send(req, stream=True)
    except Exception:
        await client.aclose()
        raise

    async def body_iter() -> Any:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=response.status_code,
        headers=_response_headers_from_httpx(response),
        media_type=response.headers.get("content-type"),
    )


async def handle_tcp_mode_get(_: Request) -> JSONResponse:
    return JSONResponse({"mode": _read_mode()})


async def handle_tcp_mode_post(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    mode = data.get("mode", "")
    if mode not in ("shadow", "live"):
        return JSONResponse({"error": "mode must be shadow or live"}, status_code=400)
    _write_mode(mode)
    return JSONResponse({"mode": mode, "ok": True})


async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/tcp/mode", handle_tcp_mode_get, methods=["GET"]),
            Route("/tcp/mode", handle_tcp_mode_post, methods=["POST"]),
            Route("/v1/messages", proxy_post_messages, methods=["POST"]),
            Route("/{path:path}", proxy_pass_through),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP-CC Proxy for Claude Code")
    parser.add_argument(
        "--host",
        default=os.environ.get("TCP_CC_PROXY_HOST", "127.0.0.1"),
        help="Bind address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TCP_CC_PROXY_PORT", "8742")),
        help="Listen port (set ANTHROPIC_BASE_URL=http://host:port)",
    )
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(
        build_app(),
        host=args.host,
        port=args.port,
        log_level="info",
        timeout_keep_alive=600,
    )


app = build_app()
