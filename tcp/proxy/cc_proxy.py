"""HTTP proxy: Claude Code → Anthropic with TCP gating (shadow or live).

Runtime defaults: file, network, and stdin are enabled unless you explicitly
disable them via TCP_PROXY_* env vars. A previous default of network_enabled=False
rejected every tool that carries SUPPORTS_NETWORK (including Bash), which breaks
Claude Code in live mode.
"""

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
from tcp.proxy.pack_manifest import (
    DEFAULT_ACTIVE_MCP_SERVERS,
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
    default_manifest_path,
    load_pack_manifest,
    pack_context_from_env,
    resolve_pack_decisions,
)
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
        # Never forward client-derived lengths: live mode rewrites JSON so byte size
        # changes; httpx must set Content-Length from the bytes we actually send.
        "content-length",
    }
)


VALID_MODES = ("shadow", "live", "live-strict")


def _read_mode() -> str:
    if MODE_PATH.exists():
        raw = MODE_PATH.read_text(encoding="utf-8").strip().lower()
        if raw in VALID_MODES:
            return raw
    env = os.environ.get("TCP_CC_PROXY_MODE", "shadow").strip().lower()
    return env if env in VALID_MODES else "shadow"


def _write_mode(mode: str) -> None:
    PROXY_STATE_DIR.mkdir(parents=True, exist_ok=True)
    MODE_PATH.write_text(mode + "\n", encoding="utf-8")


# ── Budget-aware MCP server filtering ─────────────────────────────────────────
# MCP servers whose tools are always relevant in a coding/development session.
# Tools from unlisted servers are removed in live mode unless they are rescued by
# a workspace-local allow or an explicit prompt mention.
# Non-MCP built-ins are never affected by this filter.
# Configurable via:
#   TCP_PROXY_ALLOWED_MCP_SERVERS           - hard allow boundary
#   TCP_PROXY_WORKSPACE_MCP_SERVERS         - workspace-local visibility floor
#   TCP_PROXY_PACK_MANIFEST                 - explicit manifest path

_DEFAULT_ALLOWED_MCP_SERVERS = DEFAULT_ACTIVE_MCP_SERVERS
_PACK_MANIFEST = load_pack_manifest()


def _split_csv_env(raw: str | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _get_allowed_mcp_servers() -> tuple[frozenset[str], bool]:
    """Return the hard allow boundary and whether it was explicitly overridden."""
    env = os.environ.get("TCP_PROXY_ALLOWED_MCP_SERVERS")
    if env is not None:
        return _split_csv_env(env), True
    return _DEFAULT_ALLOWED_MCP_SERVERS, False


def _get_workspace_allowed_mcp_servers() -> frozenset[str]:
    """Return MCP servers that must stay at least visible in this workspace."""
    return _split_csv_env(os.environ.get("TCP_PROXY_WORKSPACE_MCP_SERVERS"))


def _extract_mcp_server(tool_name: str) -> str | None:
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) < 2:
        return None
    server = parts[1].strip()
    return server or None


def _prompt_mentions_server(prompt: str, tool_name: str) -> bool:
    """Treat exact tool ids or server/family names as an explicit rescue signal."""
    prompt_l = prompt.lower()
    tool_l = tool_name.lower()
    if tool_l and tool_l in prompt_l:
        return True
    server = _extract_mcp_server(tool_name)
    if not server:
        return False
    server_l = server.lower()
    server_tokens = {
        server_l,
        server_l.replace("-", " "),
        server_l.replace("_", " "),
    }
    return any(token in prompt_l for token in server_tokens)


def _is_mcp_server_allowed(tool_name: str, allowed: frozenset[str]) -> bool:
    """Check if an MCP tool belongs to an allowed server."""
    if not tool_name.startswith("mcp__"):
        return True  # non-MCP tools are never filtered by this mechanism
    parts = tool_name.split("__")
    if len(parts) < 2:
        return True
    server = parts[1]
    return server in allowed


# ── Safety floor: core local coding tools that must survive live filtering ────

_SAFETY_FLOOR_TOOLS = frozenset({
    "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "Bash",
    "Agent", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion",
    "Skill", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "NotebookEdit", "Think",
    # MCP filesystem / git equivalents
    "mcp__filesystem__read_file", "mcp__filesystem__write_file",
    "mcp__filesystem__read_multiple_files", "mcp__filesystem__list_directory",
    "mcp__filesystem__search_files", "mcp__filesystem__directory_tree",
    "mcp__filesystem__create_directory", "mcp__filesystem__list_directory_with_sizes",
    "mcp__filesystem__get_file_info",
    "mcp__git__git_log", "mcp__git__git_diff", "mcp__git__git_status",
    "mcp__git__git_show", "mcp__git__git_branch",
    "mcp__git__git_diff_staged", "mcp__git__git_diff_unstaged",
    "mcp__git__git_add", "mcp__git__git_commit", "mcp__git__git_checkout",
    "mcp__git__git_reset", "mcp__git__git_create_branch",
})


def _session_from_env() -> SessionStartEvent:
    return SessionStartEvent(
        session_id="tcp_cc_proxy",
        permission_mode=os.environ.get("TCP_PROXY_PERMISSION_MODE", "default"),
        cwd=os.environ.get("TCP_PROXY_CWD", os.getcwd()),
    )


def _runtime_from_env() -> RuntimeEnvironment:
    """Match unrestricted Claude Code unless the user tightens the sandbox with env."""

    def _bool_env(key: str, *, default: bool) -> bool:
        raw = os.environ.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        v = str(raw).strip().lower()
        if v in ("0", "false", "no", "off"):
            return False
        if v in ("1", "true", "yes", "on"):
            return True
        return default

    return RuntimeEnvironment(
        network_enabled=_bool_env("TCP_PROXY_NETWORK", default=True),
        file_access_enabled=_bool_env("TCP_PROXY_FILE_ACCESS", default=True),
        stdin_enabled=_bool_env("TCP_PROXY_STDIN", default=True),
    )


def _tool_name(tool: Mapping[str, Any]) -> str:
    n = tool.get("name")
    return str(n) if n is not None else ""


def _process_tools_array(
    tools: list[Any],
    body: Mapping[str, Any],
    mode: str,
) -> tuple[list[Any], dict[str, Any]]:
    """Run 4-stage projection + gating pipeline.

    Stages (live/live-strict only; shadow logs but returns all tools):
      1. Hard environment gating     — deterministic, can reject (network/file/stdin off)
      2. Budget-aware server shaping — remove MCP tools from irrelevant servers
      3. Heuristic scoring           — audit/ranking metadata, never prunes
      4. Safety floor                — guarantees core coding tools survive

    ``live`` (default): Stages 1+2+4. Server-level filtering removes domain
    tools (Proxmox, Playwright, Tally, etc.) while preserving all coding tools.
    ``live-strict``: Stages 1+2 + full capability-flag gating (benchmark-style).
    ``shadow``: Logs all stages, returns original tools unchanged.
    """
    messages = body.get("messages")
    prompt = extract_task_prompt(messages if isinstance(messages, list) else None)
    session = _session_from_env()
    tsel = derive_request(prompt, session)
    env = _runtime_from_env()

    # ── Project all tools ────────────────────────────────────────────────
    entries: list[tuple[Any, Any, Any, Any]] = []
    records: list[Any] = []
    tiers: list[Any] = []
    for t in tools:
        if not isinstance(t, Mapping):
            entries.append((t, None, None, None))
            continue
        rec, tier = project_single_anthropic_tool(t)
        records.append(rec)
        tiers.append(tier)
        entries.append((t, rec, tier, rec.tool_name))

    # ── Stage 1: Hard environment gating ─────────────────────────────────
    # Use only environment-derived hard flags (not prompt heuristics).
    hard_tsel = ToolSelectionRequest.from_kwargs(
        required_capability_flags=tsel.hard_capability_flags,
        require_auto_approval=tsel.require_auto_approval,
    )
    gate = gate_tools(records, hard_tsel, env) if records else None

    stage1_survivors: set[str] = set()
    if gate:
        stage1_survivors = {x.tool_name for x in gate.approved_tools} | {
            x.tool_name for x in gate.approval_required_tools
        }

    # ── Stage 2: Budget-aware server-level filtering ────────────────────
    # In live mode: remove MCP tools from servers not in the allowed set.
    # Non-MCP built-ins are never affected. Safety floor is applied later.
    # In live-strict: also apply full capability flag gating (benchmark-style).
    allowed_servers, hard_allow_override = _get_allowed_mcp_servers()
    workspace_allowed_servers = (
        frozenset() if hard_allow_override else _get_workspace_allowed_mcp_servers()
    )
    pack_context = pack_context_from_env(
        cwd=session.cwd,
        profile=(
            os.environ.get("TCP_PROXY_WORKSPACE_PROFILE")
            or os.environ.get("TCP_PROXY_PROFILE")
        ),
        workspace_allowed_servers=workspace_allowed_servers,
    )
    pack_decisions, server_pack_decisions = resolve_pack_decisions(
        _PACK_MANIFEST,
        pack_context,
    )
    stage2_survivors = set(stage1_survivors)
    server_filtered: set[str] = set()
    workspace_rescued: set[str] = set()
    deferred_visible: set[str] = set()
    explicit_rescued: set[str] = set()
    server_allow_source: dict[str, str] = {}

    if mode in ("live", "live-strict"):
        for name in stage1_survivors:
            server = _extract_mcp_server(name)
            if _is_mcp_server_allowed(name, allowed_servers):
                if server:
                    server_allow_source.setdefault(server, "hard_allow")
                continue
            if hard_allow_override:
                stage2_survivors.discard(name)
                server_filtered.add(name)
                continue
            pack_decision = None if server is None else server_pack_decisions.get(server)
            pack_state = (
                pack_decision.state if pack_decision is not None else STATE_SUPPRESSED
            )
            if server and pack_state == STATE_ACTIVE:
                server_allow_source.setdefault(server, "pack_active")
                continue
            if server and pack_state == STATE_DEFERRED:
                deferred_visible.add(name)
                server_allow_source.setdefault(server, "workspace_allow")
                if "workspace_allow" in pack_decision.reasons:
                    workspace_rescued.add(name)
                continue
            if server and _prompt_mentions_server(prompt, name):
                explicit_rescued.add(name)
                server_allow_source.setdefault(server, "explicit_request")
                continue
            stage2_survivors.discard(name)
            server_filtered.add(name)

    if mode == "live-strict" and records:
        strict_tsel = ToolSelectionRequest.from_kwargs(
            required_capability_flags=tsel.required_capability_flags,
            required_commands=set(tsel.required_commands) or None,
            required_input_formats=set(tsel.required_input_formats) or None,
            required_output_formats=set(tsel.required_output_formats) or None,
            required_processing_modes=set(tsel.required_processing_modes) or None,
            require_auto_approval=tsel.require_auto_approval,
        )
        strict_gate = gate_tools(records, strict_tsel, env)
        stage2_survivors = {x.tool_name for x in strict_gate.approved_tools} | {
            x.tool_name for x in strict_gate.approval_required_tools
        }

    # ── Stage 3: Heuristic scoring (audit only, never prunes) ────────────
    # Record what prompt-derived flags would have done, for telemetry.
    heuristic_would_reject: set[str] = set()
    if tsel.heuristic_capability_flags and gate:
        for rec_item in records:
            if tsel.heuristic_capability_flags and (
                rec_item.capability_flags & tsel.heuristic_capability_flags
            ) != tsel.heuristic_capability_flags:
                heuristic_would_reject.add(rec_item.tool_name)

    # ── Stage 4: Safety floor ────────────────────────────────────────────
    # Ensure core coding tools survive unless Stage 1 made them
    # environmentally impossible.
    active_survivors = set(stage2_survivors)
    safety_floor_activated = False
    floor_rescued: set[str] = set()

    if mode == "live" and env.file_access_enabled:
        all_names = {rec.tool_name for (_, rec, _, _) in entries if rec is not None}
        floor_names = _SAFETY_FLOOR_TOOLS & all_names
        missing_floor = floor_names - active_survivors
        if missing_floor:
            safety_floor_activated = True
            floor_rescued = missing_floor
            active_survivors = active_survivors | missing_floor

    # ── Build output tool list ───────────────────────────────────────────
    live_tools: list[Any] = []
    for item in entries:
        orig, rec, tier, _name = item
        if rec is None:
            live_tools.append(orig)
            continue
        if rec.tool_name in active_survivors:
            live_tools.append(orig)
        elif tier == ProjectionTier.FALLBACK and rec.tool_name not in server_filtered:
            # FALLBACK tools pass through unless explicitly server-filtered
            live_tools.append(orig)

    # ── Serialize audit log ──────────────────────────────────────────────
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

    # ── Decision metadata ────────────────────────────────────────────────
    meta: dict[str, Any] = {
        "mode": mode,
        "strategy": "conservative" if mode == "live" else ("strict" if mode == "live-strict" else "shadow"),
        "prompt_excerpt": prompt[:240],
        "required_capability_flags": tsel.required_capability_flags,
        "hard_capability_flags": tsel.hard_capability_flags,
        "heuristic_capability_flags": tsel.heuristic_capability_flags,
        "tool_count_before": len(tools),
        "stage1_survivor_count": len(stage1_survivors),
        "stage2_survivor_count": len(stage2_survivors),
        "server_filtered_count": len(server_filtered),
        "server_filtered": sorted(server_filtered) if server_filtered else [],
        "pack_manifest_source": _PACK_MANIFEST.source_path,
        "pack_manifest_default_path": str(default_manifest_path()),
        "pack_states": {
            pack_id: decision.state
            for pack_id, decision in sorted(pack_decisions.items())
        },
        "pack_activation_reasons": {
            pack_id: list(decision.reasons)
            for pack_id, decision in sorted(pack_decisions.items())
        },
        "active_packs": sorted(
            pack_id for pack_id, decision in pack_decisions.items() if decision.state == STATE_ACTIVE
        ),
        "deferred_packs": sorted(
            pack_id for pack_id, decision in pack_decisions.items() if decision.state == STATE_DEFERRED
        ),
        "suppressed_packs": sorted(
            pack_id for pack_id, decision in pack_decisions.items() if decision.state == STATE_SUPPRESSED
        ),
        "workspace_allowed_servers": sorted(workspace_allowed_servers),
        "workspace_rescued": sorted(workspace_rescued) if workspace_rescued else [],
        "deferred_visible": sorted(deferred_visible) if deferred_visible else [],
        "explicit_server_rescued": sorted(explicit_rescued) if explicit_rescued else [],
        "server_allow_source": dict(sorted(server_allow_source.items())),
        "heuristic_would_reject_count": len(heuristic_would_reject),
        "heuristic_would_reject": sorted(heuristic_would_reject) if heuristic_would_reject else [],
        "safety_floor_activated": safety_floor_activated,
        "safety_floor_rescued": sorted(floor_rescued) if floor_rescued else [],
        "tool_count_after": len(live_tools) if mode in ("live", "live-strict") else len(tools),
        # Backward-compat aliases for TCP-MT-10 / shadow pilot scripts.
        "full_tool_count": len(tools),
        "survivor_count": len(active_survivors),
        "survivor_names_sorted": sorted(active_survivors),
        "projection_tiers": [
            tier.name for (_o, _r, tier, _n) in entries if tier is not None
        ],
        "audit": audit_serial,
    }

    # ── Empty-set guardrail ──────────────────────────────────────────────
    if mode in ("live", "live-strict") and len(tools) > 0 and len(live_tools) == 0:
        meta["live_empty_fallback"] = True
        meta["tool_count_after"] = len(tools)
        return list(tools), meta

    if mode in ("live", "live-strict"):
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


def _streaming_response_headers(response: httpx.Response) -> dict[str, str]:
    """Headers when piping httpx ``aiter_raw()`` to the client.

    Drop ``content-length`` so uvicorn/chunked encoding matches how we stream;
    keep ``content-encoding`` so the client decompresses wire-format once.
    """
    hdrs = _response_headers_from_httpx(response)
    return {k: v for k, v in hdrs.items() if k.lower() != "content-length"}


def _buffered_response_headers(response: httpx.Response, body: bytes) -> dict[str, str]:
    """Headers after ``aread()`` — httpx has already decoded Content-Encoding."""
    hdrs = _response_headers_from_httpx(response)
    drop = frozenset({"content-encoding", "content-length", "transfer-encoding"})
    out = {k: v for k, v in hdrs.items() if k.lower() not in drop}
    out["content-length"] = str(len(body))
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
    if request.url.query:
        url = f"{url}?{request.url.query}"
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
                # Wire bytes only — aiter_bytes() would gzip-decode here and break
                # clients that still see Content-Encoding: gzip (ZlibError).
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            body_iter(),
            status_code=response.status_code,
            headers=_streaming_response_headers(response),
            media_type=response.headers.get("content-type"),
        )

    try:
        content = await response.aread()
        hdrs = _buffered_response_headers(response, content)
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
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=response.status_code,
        headers=_streaming_response_headers(response),
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
    if mode not in VALID_MODES:
        return JSONResponse({"error": f"mode must be one of: {', '.join(VALID_MODES)}"}, status_code=400)
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

if __name__ == "__main__":
    main()
