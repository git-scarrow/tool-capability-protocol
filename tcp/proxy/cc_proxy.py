"""HTTP proxy: Claude Code → Anthropic with TCP gating (shadow or live).

Runtime defaults: file, network, and stdin are enabled unless you explicitly
disable them via TCP_PROXY_* env vars. A previous default of network_enabled=False
rejected every tool that carries SUPPORTS_NETWORK (including Bash), which breaks
Claude Code in live mode.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from tcp.derivation.request_derivation import SessionStartEvent, derive_request
from tcp.harness.gating import RuntimeEnvironment, gate_tools
from tcp.harness.models import ToolSelectionRequest
from tcp.proxy.controller import (
    ToolPackController,
    TPC_RULE_HEURISTIC_UPGRADE,
    _server_alias_tokens,
)
from tcp.proxy.pack_manifest import (
    DEFAULT_ACTIVE_MCP_SERVERS,
    PackInspection,
    PackManifestError,
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
    default_manifest_path,
    inspect_pack_state,
    load_pack_manifest,
    pack_context_from_env,
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
UPSTREAM_TIMEOUT = httpx.Timeout(600.0, connect=30.0)
UPSTREAM_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)
UPSTREAM_RETRY_MAX_ATTEMPTS = 2
SAFE_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


# _server_alias_tokens and server-level prompt matching are in tcp.proxy.controller.


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

_SAFETY_FLOOR_TOOLS = frozenset(
    {
        "Read",
        "Edit",
        "MultiEdit",
        "Write",
        "Glob",
        "Grep",
        "Bash",
        "Agent",
        "EnterPlanMode",
        "ExitPlanMode",
        "AskUserQuestion",
        "Skill",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "NotebookEdit",
        "Think",
        # MCP filesystem / git equivalents
        "mcp__filesystem__read_file",
        "mcp__filesystem__write_file",
        "mcp__filesystem__read_multiple_files",
        "mcp__filesystem__list_directory",
        "mcp__filesystem__search_files",
        "mcp__filesystem__directory_tree",
        "mcp__filesystem__create_directory",
        "mcp__filesystem__list_directory_with_sizes",
        "mcp__filesystem__get_file_info",
        "mcp__git__git_log",
        "mcp__git__git_diff",
        "mcp__git__git_status",
        "mcp__git__git_show",
        "mcp__git__git_branch",
        "mcp__git__git_diff_staged",
        "mcp__git__git_diff_unstaged",
        "mcp__git__git_add",
        "mcp__git__git_commit",
        "mcp__git__git_checkout",
        "mcp__git__git_reset",
        "mcp__git__git_create_branch",
    }
)


_DEFERRED_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}


def _session_from_env() -> SessionStartEvent:
    return SessionStartEvent(
        session_id="tcp_cc_proxy",
        permission_mode=os.environ.get("TCP_PROXY_PERMISSION_MODE", "default"),
        cwd=os.environ.get("TCP_PROXY_CWD", os.getcwd()),
    )


def _deferred_tool_surface(
    tool: Mapping[str, Any],
    *,
    pack_id: str | None,
    server: str | None,
    reason: str | None,
) -> dict[str, Any]:
    """Return a minimal visible representation for a deferred tool surface.

    The local runtime still knows the full tool definition; this only shrinks the
    model-visible schema surface sent upstream.
    """
    name = _tool_name(tool)
    pack_label = pack_id or "deferred-pack"
    server_label = server or "deferred-server"
    reason_suffix = f" via {reason}" if reason else ""
    return {
        "name": name,
        "description": (
            f"Deferred schema for {name} ({pack_label}/{server_label}{reason_suffix})."
        ),
        "input_schema": dict(_DEFERRED_INPUT_SCHEMA),
    }


# Schema materialization state is now derived from ToolPackController.server_state()
# in _process_tools_array — see controller_decisions lookup below.


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


def _short_sha256_json(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _manifest_hash() -> str:
    return _short_sha256_json(
        {
            "version": _PACK_MANIFEST.version,
            "source_path": _PACK_MANIFEST.source_path,
            "packs": [
                {
                    "pack_id": pack.pack_id,
                    "servers": sorted(pack.servers),
                    "default_state": pack.default_state,
                    "allow_workspace": pack.allow_workspace,
                    "active_workspaces": sorted(pack.active_workspaces),
                    "active_profiles": sorted(pack.active_profiles),
                    "active_env": {
                        key: sorted(values)
                        for key, values in sorted(pack.active_env.items())
                    },
                }
                for pack in _PACK_MANIFEST.packs
            ],
        }
    )


def _max_description_similarity_proxy(tools: list[Any]) -> float:
    """Max pairwise SequenceMatcher ratio between surviving tool descriptions.

    Returns a value in [0.0, 1.0]. Used as a proxy for how confusable the
    survivor tool set is — a high value suggests ambiguous-lane selection risk.
    """
    descriptions = [
        t.get("description", "")
        for t in tools
        if isinstance(t, Mapping) and t.get("description")
    ]
    if len(descriptions) < 2:
        return 0.0
    max_sim = 0.0
    for i in range(len(descriptions)):
        for j in range(i + 1, len(descriptions)):
            sim = difflib.SequenceMatcher(
                None, descriptions[i].lower(), descriptions[j].lower()
            ).ratio()
            if sim > max_sim:
                max_sim = sim
    return max_sim


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
    controller = ToolPackController(
        _PACK_MANIFEST,
        pack_context,
        allowed_servers=allowed_servers,
        hard_allow_override=hard_allow_override,
    )
    pack_decisions = controller.pack_decisions

    # Pre-resolve all unique servers seen in the tool list (one pass, cached).
    all_tool_servers: frozenset[str] = frozenset(
        s
        for (_, rec, _, _) in entries
        if rec is not None
        for s in (_extract_mcp_server(rec.tool_name),)
        if s is not None
    )
    controller_decisions: dict[str, Any] = controller.bulk_resolve(
        all_tool_servers, prompt=prompt
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
            if server is None:
                # Non-MCP built-in: always passes through Stage 2.
                continue
            decision = controller_decisions.get(server)
            if decision is None:
                # Unknown server not in tool list — should not happen, but safe fallback.
                stage2_survivors.discard(name)
                server_filtered.add(name)
                continue
            if decision.state == STATE_SUPPRESSED:
                stage2_survivors.discard(name)
                server_filtered.add(name)
            elif decision.state == STATE_DEFERRED:
                deferred_visible.add(name)
                server_allow_source.setdefault(server, decision.legacy_allow_source)
                if decision.tpc_rule == TPC_RULE_HEURISTIC_UPGRADE:
                    explicit_rescued.add(name)
                else:
                    workspace_rescued.add(name)
            else:  # ACTIVE
                server_allow_source.setdefault(server, decision.legacy_allow_source)

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
            if (
                tsel.heuristic_capability_flags
                and (rec_item.capability_flags & tsel.heuristic_capability_flags)
                != tsel.heuristic_capability_flags
            ):
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
    materialized_schema_tools: list[str] = []
    deferred_schema_tools: list[str] = []
    surface_state_by_tool: dict[str, str] = {}
    for item in entries:
        orig, rec, tier, _name = item
        if rec is None:
            live_tools.append(orig)
            continue
        survives = rec.tool_name in active_survivors or (
            tier == ProjectionTier.FALLBACK and rec.tool_name not in server_filtered
        )
        if not survives:
            continue

        tool_server = _extract_mcp_server(rec.tool_name)
        ctrl_decision = (
            None if tool_server is None else controller_decisions.get(tool_server)
        )
        schema_state = (
            STATE_ACTIVE
            if ctrl_decision is None
            else ctrl_decision.state
        )
        surface_state_by_tool[rec.tool_name] = schema_state

        if mode in ("live", "live-strict") and schema_state == STATE_DEFERRED:
            live_tools.append(
                _deferred_tool_surface(
                    orig,
                    pack_id=(
                        None if ctrl_decision is None else ctrl_decision.pack_id
                    ),
                    server=tool_server,
                    reason=(
                        server_allow_source.get(tool_server) if tool_server else None
                    ),
                )
            )
            deferred_schema_tools.append(rec.tool_name)
            continue

        live_tools.append(orig)
        materialized_schema_tools.append(rec.tool_name)

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
        "strategy": (
            "conservative"
            if mode == "live"
            else ("strict" if mode == "live-strict" else "shadow")
        ),
        "prompt_excerpt": prompt[:240],
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        "workspace_path": session.cwd,
        "workspace_name": pack_context.workspace_name,
        "resolved_profile": pack_context.profile,
        "required_capability_flags": tsel.required_capability_flags,
        "hard_capability_flags": tsel.hard_capability_flags,
        "heuristic_capability_flags": tsel.heuristic_capability_flags,
        "tool_count_before": len(tools),
        "stage1_survivor_count": len(stage1_survivors),
        "stage2_survivor_count": len(stage2_survivors),
        "server_filtered_count": len(server_filtered),
        "server_filtered": sorted(server_filtered) if server_filtered else [],
        "pack_manifest_source": _PACK_MANIFEST.source_path,
        "pack_manifest_hash": _manifest_hash(),
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
            pack_id
            for pack_id, decision in pack_decisions.items()
            if decision.state == STATE_ACTIVE
        ),
        "deferred_packs": sorted(
            pack_id
            for pack_id, decision in pack_decisions.items()
            if decision.state == STATE_DEFERRED
        ),
        "suppressed_packs": sorted(
            pack_id
            for pack_id, decision in pack_decisions.items()
            if decision.state == STATE_SUPPRESSED
        ),
        "workspace_allowed_servers": sorted(workspace_allowed_servers),
        "hard_allowed_servers": sorted(allowed_servers),
        "workspace_rescued": sorted(workspace_rescued) if workspace_rescued else [],
        "deferred_visible": sorted(deferred_visible) if deferred_visible else [],
        "explicit_server_rescued": sorted(explicit_rescued) if explicit_rescued else [],
        "server_allow_source": dict(sorted(server_allow_source.items())),
        "tpc_rule_by_server": {
            s: d.tpc_rule
            for s, d in sorted(controller_decisions.items())
            if d.state != STATE_SUPPRESSED
        },
        "heuristic_would_reject_count": len(heuristic_would_reject),
        "heuristic_would_reject": (
            sorted(heuristic_would_reject) if heuristic_would_reject else []
        ),
        "safety_floor_activated": safety_floor_activated,
        "safety_floor_rescued": sorted(floor_rescued) if floor_rescued else [],
        "materialized_schema_count": len(materialized_schema_tools),
        "materialized_schema_tools": sorted(materialized_schema_tools),
        "deferred_schema_count": len(deferred_schema_tools),
        "deferred_schema_tools": sorted(deferred_schema_tools),
        "surface_state_by_tool": dict(sorted(surface_state_by_tool.items())),
        "tool_surface_bytes_before": len(
            json.dumps(tools, sort_keys=True, default=str)
        ),
        "tool_surface_bytes_after": len(
            json.dumps(live_tools, sort_keys=True, default=str)
        ),
        "tool_count_after": (
            len(live_tools) if mode in ("live", "live-strict") else len(tools)
        ),
        # Backward-compat aliases for TCP-MT-10 / shadow pilot scripts.
        "full_tool_count": len(tools),
        "survivor_count": len(active_survivors),
        "survivor_names_sorted": sorted(active_survivors),
        "projection_tiers": [
            tier.name for (_o, _r, tier, _n) in entries if tier is not None
        ],
        "audit": audit_serial,
        # TCP-MT-12: proactive first-tool-miss telemetry fields
        # ambiguous_lane: true when ≥2 tools survive to active status,
        # meaning LLM must disambiguate without gating help.
        "ambiguous_lane": len(active_survivors) >= 2,
        # schema_load_on_demand: true when any surviving tool's schema was
        # deferred (sent as minimal surface), requiring on-demand expansion.
        "schema_load_on_demand": len(deferred_schema_tools) > 0,
        # pack_promotion_triggered: true when a pack was rescued from
        # suppressed/deferred to visible state by workspace or explicit rules.
        "pack_promotion_triggered": bool(workspace_rescued or explicit_rescued),
        # description_similarity_max: max pairwise SequenceMatcher ratio
        # between active-survivor tool descriptions. Measures confusability
        # of the survivor set.
        # first_tool_name / expected_tool_name / first_tool_correct are
        # populated by the response tap in proxy_post_messages after the
        # upstream response is observed.
        "description_similarity_max": _max_description_similarity_proxy(
            [
                orig
                for (orig, rec, _tier, _name) in entries
                if rec is not None and rec.tool_name in active_survivors
            ]
        ),
        # TCP-IMP-17: prompt-similarity ranking — top survivor regardless of count.
        # Populated when the task prompt is non-empty and any survivors exist.
        "top_survivor_by_similarity": _top_survivor_by_prompt_similarity(
            prompt,
            [
                orig
                for (orig, rec, _tier, _name) in entries
                if rec is not None and rec.tool_name in active_survivors
            ],
        ),
    }

    # ── Empty-set guardrail ──────────────────────────────────────────────
    if mode in ("live", "live-strict") and len(tools) > 0 and len(live_tools) == 0:
        meta["live_empty_fallback"] = True
        meta["tool_count_after"] = len(tools)
        return list(tools), meta

    if mode in ("live", "live-strict"):
        return live_tools, meta
    return list(tools), meta


def _maybe_transform_messages_body(
    raw: bytes, mode: str
) -> tuple[bytes, dict[str, Any] | None]:
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


# ── Response tap helpers ───────────────────────────────────────────────────────


_EXPECTED_TOOL_MAX_SURVIVORS: int = 3  # TCP-IMP-16: count-based fallback threshold


def _top_survivor_by_prompt_similarity(
    prompt: str | None,
    tools: list[Any],
) -> str | None:
    """Return the survivor tool name with the highest prompt similarity.

    Computes SequenceMatcher ratio between the task prompt and each tool's
    ``name + description`` text, returning the argmax.  Returns None when
    prompt is empty/None or tools list is empty.

    TCP-IMP-17: produces expected_tool_name on every turn with a non-empty
    prompt, regardless of survivor_count.
    """
    if not prompt or not tools:
        return None
    prompt_lc = prompt.lower()
    best_name: str | None = None
    best_score = -1.0
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        name = tool.get("name", "") or ""
        desc = tool.get("description", "") or ""
        candidate = f"{name} {desc}".lower()
        score = difflib.SequenceMatcher(None, prompt_lc, candidate).ratio()
        if score > best_score:
            best_score = score
            best_name = name if isinstance(name, str) else None
    return best_name


def _compute_expected_tool_name(meta: dict[str, Any] | None) -> str | None:
    """Derive expected first tool from request-side survivor metadata.

    Priority (TCP-IMP-17):
      1. top_survivor_by_similarity  → prompt-ranked expectation (all survivor counts)
      2. count-based fallback (k=3)  → first sorted survivor when count ≤ k
      3. None                        → no expectation derivable
    """
    if not meta:
        return None
    # TCP-IMP-17: similarity ranking covers the high-survivor-count majority
    top_by_sim = meta.get("top_survivor_by_similarity")
    if top_by_sim is not None:
        return top_by_sim if isinstance(top_by_sim, str) else None
    # TCP-IMP-16: count-based fallback for tight shortlists
    count = meta.get("survivor_count", 0)
    survivors = meta.get("survivor_names_sorted", [])
    if 1 <= count <= _EXPECTED_TOOL_MAX_SURVIVORS and len(survivors) >= 1:
        return survivors[0]
    return None


def _first_tool_from_response_body(body: bytes) -> str | None:
    """Extract first tool_use block name from a non-streamed Anthropic response."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            return name if isinstance(name, str) else None
    return None


def _first_tool_from_sse_buf(buf: bytes) -> tuple[str | None, bool]:
    """Scan an SSE byte buffer for the first tool_use content block.

    Returns ``(tool_name, stream_ended)`` where:
      - ``tool_name`` is the name of the first tool_use block, or None
      - ``stream_ended`` is True when a ``message_stop`` event was seen

    Scans only ``data:`` lines and handles partial final lines gracefully
    (incomplete lines are skipped — they will appear in the next chunk).
    """
    try:
        text = buf.decode("utf-8", errors="replace")
    except Exception:
        return None, False

    stream_ended = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        event_type = data.get("type")
        if event_type == "content_block_start":
            cb = data.get("content_block", {})
            if isinstance(cb, dict) and cb.get("type") == "tool_use":
                name = cb.get("name")
                return (name if isinstance(name, str) else None), False
        elif event_type == "message_stop":
            stream_ended = True

    return None, stream_ended


def _write_decision_record(
    req_ts: float,
    meta: dict[str, Any],
    first_tool_name: str | None,
    tap_skipped: bool = False,
    preflight_duration_ms: float | None = None,
    upstream_request_duration_ms: float | None = None,
    first_byte_duration_ms: float | None = None,
    total_response_duration_ms: float | None = None,
    retry_count: int = 0,
) -> None:
    """Write (or rewrite) the enriched decisions.jsonl entry for this turn."""
    expected_tool_name = _compute_expected_tool_name(meta)
    first_tool_correct: bool | None = None
    if first_tool_name is not None and expected_tool_name is not None:
        first_tool_correct = first_tool_name == expected_tool_name

    _append_jsonl(
        DECISIONS_LOG,
        {
            "ts": req_ts,
            "path": "/v1/messages",
            **meta,
            "first_tool_name": first_tool_name,
            "expected_tool_name": expected_tool_name,
            "first_tool_correct": first_tool_correct,
            "tap_skipped": tap_skipped,
            "preflight_duration_ms": preflight_duration_ms,
            "upstream_request_duration_ms": upstream_request_duration_ms,
            "first_byte_duration_ms": first_byte_duration_ms,
            "total_response_duration_ms": total_response_duration_ms,
            "retry_count": retry_count,
        },
    )


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
    return os.environ.get(
        "ANTHROPIC_UPSTREAM_BASE", "https://api.anthropic.com"
    ).rstrip("/")


def _build_upstream_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, limits=UPSTREAM_LIMITS)


@asynccontextmanager
async def _app_lifespan(app: Starlette) -> Any:
    app.state.upstream_client = _build_upstream_client()
    try:
        yield
    finally:
        await app.state.upstream_client.aclose()


def _get_upstream_client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "upstream_client", None)
    if client is None or not hasattr(client, "send") or not hasattr(client, "aclose"):
        raise RuntimeError("upstream client is not initialized")
    return client


def _is_retryable_send_error(exc: Exception) -> bool:
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def _should_retry_pre_first_byte(
    *,
    method: str,
    saw_response_bytes: bool,
    exc: Exception,
) -> bool:
    if saw_response_bytes:
        return False
    if _is_retryable_send_error(exc):
        return True
    if method.upper() in SAFE_RETRY_METHODS and isinstance(exc, (httpx.ReadError, httpx.ReadTimeout)):
        return True
    return False


async def _send_upstream_with_retry(
    client: httpx.AsyncClient,
    request: httpx.Request,
) -> tuple[httpx.Response, int]:
    last_exc: Exception | None = None
    for attempt in range(UPSTREAM_RETRY_MAX_ATTEMPTS):
        try:
            response = await client.send(request, stream=True)
            return response, attempt
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= UPSTREAM_RETRY_MAX_ATTEMPTS:
                raise
            if not _should_retry_pre_first_byte(
                method=request.method,
                saw_response_bytes=False,
                exc=exc,
            ):
                raise
    assert last_exc is not None
    raise last_exc


async def _read_response_with_timing(
    response: httpx.Response,
) -> tuple[bytes, float | None]:
    chunks: list[bytes] = []
    first_byte_at: float | None = None
    async for chunk in _aiter_response_raw(response):
        if first_byte_at is None:
            first_byte_at = time.perf_counter()
        chunks.append(chunk)
    return b"".join(chunks), first_byte_at


async def _aiter_response_raw(response: httpx.Response) -> Any:
    if response.is_stream_consumed:
        body = response.content
        if body:
            yield body
        return
    async for chunk in response.aiter_raw():
        yield chunk


async def proxy_post_messages(request: Request) -> Response:
    mode = _read_mode()
    started_at = time.perf_counter()
    raw = await request.body()
    req_ts = time.time()
    transformed, meta = _maybe_transform_messages_body(raw, mode)
    preflight_done_at = time.perf_counter()
    # Decision record is written AFTER response tapping so first_tool_name,
    # expected_tool_name, and first_tool_correct can be included in one record.

    url = f"{_upstream_base()}/v1/messages"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    # Strip any client-supplied accept-encoding (case-insensitive) then force
    # identity so upstream never compresses SSE and the tap can always parse.
    headers = {
        k: v
        for k, v in _forward_headers(request).items()
        if k.lower() != "accept-encoding"
    }
    headers["Accept-Encoding"] = "identity"
    stream = False
    try:
        parsed = json.loads(transformed)
        stream = bool(parsed.get("stream")) if isinstance(parsed, dict) else False
    except json.JSONDecodeError:
        stream = False

    client = _get_upstream_client(request)
    upstream_started_at = time.perf_counter()
    upstream_request_done_at: float | None = None
    retry_count = 0
    try:
        req = client.build_request(
            "POST",
            url,
            headers=headers,
            content=transformed,
        )
        response, retry_count = await _send_upstream_with_retry(client, req)
        upstream_request_done_at = time.perf_counter()
    except Exception:
        # On upstream error: still write a decision record without tool data.
        if meta is not None:
            _write_decision_record(
                req_ts,
                meta,
                None,
                tap_skipped=True,
                preflight_duration_ms=(preflight_done_at - started_at) * 1000.0,
                upstream_request_duration_ms=(
                    (upstream_request_done_at - upstream_started_at) * 1000.0
                    if upstream_request_done_at is not None
                    else None
                ),
                total_response_duration_ms=(time.perf_counter() - started_at) * 1000.0,
                retry_count=retry_count,
            )
        raise

    if stream:
        # Determine whether we can tap the SSE stream for tool names.
        # Skip tapping if the response body is compressed — compressed SSE
        # cannot be parsed from raw bytes without decompression buffering.
        # With Accept-Encoding: identity forced above, this should always be
        # uncompressed, but we keep the guard in case of upstream surprises.
        content_enc = response.headers.get("content-encoding", "").lower()
        can_tap = meta is not None and content_enc not in ("gzip", "br", "deflate", "zstd")
        # State shared by body_iter closure.
        # _tap["buf"] holds only the unparsed tail (incomplete last line) so that
        # _first_tool_from_sse_buf never rescans already-consumed bytes (O(n) total).
        _tap: dict[str, Any] = {"buf": b"", "done": False}
        first_byte_at: float | None = None

        async def body_iter() -> Any:
            nonlocal first_byte_at
            try:
                # Wire bytes only — aiter_bytes() would gzip-decode here and break
                # clients that still see Content-Encoding: gzip (ZlibError).
                async for chunk in _aiter_response_raw(response):
                    if first_byte_at is None:
                        first_byte_at = time.perf_counter()
                    yield chunk
                    if can_tap and not _tap["done"]:
                        combined = _tap["buf"] + chunk
                        tool_name, ended = _first_tool_from_sse_buf(combined)
                        if tool_name is not None or ended:
                            # Write the decision record as soon as we know the
                            # first tool (or that the model called no tool).
                            assert meta is not None
                            _write_decision_record(
                                req_ts,
                                meta,
                                tool_name,
                                tap_skipped=False,
                                preflight_duration_ms=(preflight_done_at - started_at) * 1000.0,
                                upstream_request_duration_ms=(
                                    (upstream_request_done_at - upstream_started_at) * 1000.0
                                    if upstream_request_done_at is not None
                                    else None
                                ),
                                first_byte_duration_ms=(
                                    (first_byte_at - started_at) * 1000.0
                                    if first_byte_at is not None
                                    else None
                                ),
                                retry_count=retry_count,
                            )
                            _tap["done"] = True
                            _tap["buf"] = b""  # release buffer memory
                        else:
                            # Retain only bytes after the last newline so the
                            # next chunk completes any split line without
                            # re-scanning already-parsed data (keeps O(n) total).
                            last_nl = combined.rfind(b"\n")
                            _tap["buf"] = combined[last_nl + 1:] if last_nl >= 0 else combined
            finally:
                if can_tap and not _tap["done"]:
                    # Stream ended without a message_stop or tool event
                    # (e.g. non-200, network error). Write with null tool.
                    assert meta is not None
                    _write_decision_record(
                        req_ts,
                        meta,
                        None,
                        tap_skipped=False,
                        preflight_duration_ms=(preflight_done_at - started_at) * 1000.0,
                        upstream_request_duration_ms=(
                            (upstream_request_done_at - upstream_started_at) * 1000.0
                            if upstream_request_done_at is not None
                            else None
                        ),
                        first_byte_duration_ms=(
                            (first_byte_at - started_at) * 1000.0
                            if first_byte_at is not None
                            else None
                        ),
                        total_response_duration_ms=(time.perf_counter() - started_at) * 1000.0,
                        retry_count=retry_count,
                    )
                    _tap["done"] = True
                elif meta is not None and not can_tap:
                    # Compressed stream or no meta: write without tool data.
                    _write_decision_record(
                        req_ts,
                        meta,
                        None,
                        tap_skipped=True,
                        preflight_duration_ms=(preflight_done_at - started_at) * 1000.0,
                        upstream_request_duration_ms=(
                            (upstream_request_done_at - upstream_started_at) * 1000.0
                            if upstream_request_done_at is not None
                            else None
                        ),
                        first_byte_duration_ms=(
                            (first_byte_at - started_at) * 1000.0
                            if first_byte_at is not None
                            else None
                        ),
                        total_response_duration_ms=(time.perf_counter() - started_at) * 1000.0,
                        retry_count=retry_count,
                    )
                await response.aclose()

        return StreamingResponse(
            body_iter(),
            status_code=response.status_code,
            headers=_streaming_response_headers(response),
            media_type=response.headers.get("content-type"),
        )

    try:
        content, first_byte_at = await _read_response_with_timing(response)
        hdrs = _buffered_response_headers(response, content)
        # Non-streaming: extract first tool from the full response body.
        first_tool = _first_tool_from_response_body(content) if meta is not None else None
        if meta is not None:
            _write_decision_record(
                req_ts,
                meta,
                first_tool,
                tap_skipped=False,
                preflight_duration_ms=(preflight_done_at - started_at) * 1000.0,
                upstream_request_duration_ms=(
                    (upstream_request_done_at - upstream_started_at) * 1000.0
                    if upstream_request_done_at is not None
                    else None
                ),
                first_byte_duration_ms=(
                    (first_byte_at - started_at) * 1000.0
                    if first_byte_at is not None
                    else None
                ),
                total_response_duration_ms=(time.perf_counter() - started_at) * 1000.0,
                retry_count=retry_count,
            )
        return Response(
            content=content,
            status_code=response.status_code,
            headers=hdrs,
        )
    finally:
        await response.aclose()


async def proxy_pass_through(request: Request) -> Response:
    """Forward non-/v1/messages requests unchanged (same verb, path, body)."""
    raw = await request.body()
    url = f"{_upstream_base()}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = _forward_headers(request)
    client = _get_upstream_client(request)
    first_byte_seen = False
    try:
        req = client.build_request(request.method, url, headers=headers, content=raw)
        response, _retry_count = await _send_upstream_with_retry(client, req)
    except Exception:
        raise

    async def body_iter() -> Any:
        nonlocal first_byte_seen
        try:
            async for chunk in _aiter_response_raw(response):
                first_byte_seen = True
                yield chunk
        except Exception as exc:
            if _retry_count == 0 and _should_retry_pre_first_byte(
                method=request.method,
                saw_response_bytes=first_byte_seen,
                exc=exc,
            ):
                retry_req = client.build_request(
                    request.method,
                    url,
                    headers=headers,
                    content=raw,
                )
                retry_response, _ = await _send_upstream_with_retry(client, retry_req)
                try:
                    async for retry_chunk in _aiter_response_raw(retry_response):
                        yield retry_chunk
                finally:
                    await retry_response.aclose()
                return
            raise
        finally:
            await response.aclose()

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
        return JSONResponse(
            {"error": f"mode must be one of: {', '.join(VALID_MODES)}"}, status_code=400
        )
    _write_mode(mode)
    return JSONResponse({"mode": mode, "ok": True})


async def health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    return Starlette(
        lifespan=_app_lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/tcp/mode", handle_tcp_mode_get, methods=["GET"]),
            Route("/tcp/mode", handle_tcp_mode_post, methods=["POST"]),
            Route("/v1/messages", proxy_post_messages, methods=["POST"]),
            Route("/{path:path}", proxy_pass_through, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"]),
        ],
    )


def _doctor_payload(inspection: PackInspection) -> dict[str, Any]:
    return {
        "manifest_source": inspection.manifest_source,
        "default_manifest_path": inspection.default_manifest_path,
        "explicit_manifest_path": inspection.explicit_manifest_path,
        "explicit_manifest_in_effect": inspection.explicit_manifest_in_effect,
        "workspace_name": inspection.workspace_name,
        "workspace_path": inspection.workspace_path,
        "profile": inspection.profile,
        "workspace_allowed_servers": list(inspection.workspace_allowed_servers),
        "packs": [
            {
                "pack_id": decision.pack_id,
                "state": decision.state,
                "reasons": list(decision.reasons),
                "servers": list(decision.servers),
            }
            for decision in inspection.pack_decisions
        ],
    }


def _render_doctor_text(inspection: PackInspection) -> str:
    lines = [
        "TCP proxy pack manifest doctor",
        f"manifest_source: {inspection.manifest_source}",
        f"default_manifest_path: {inspection.default_manifest_path}",
        f"explicit_manifest_path: {inspection.explicit_manifest_path or '-'}",
        f"explicit_manifest_in_effect: {'yes' if inspection.explicit_manifest_in_effect else 'no'}",
        f"workspace_name: {inspection.workspace_name}",
        f"workspace_path: {inspection.workspace_path}",
        f"profile: {inspection.profile}",
        "workspace_allowed_servers: "
        + (", ".join(inspection.workspace_allowed_servers) or "-"),
        "",
        "packs:",
    ]
    for decision in inspection.pack_decisions:
        lines.append(f"  - {decision.pack_id}: {decision.state}")
        lines.append(f"    servers: {', '.join(decision.servers)}")
        lines.append(f"    reasons: {', '.join(decision.reasons)}")
    return "\n".join(lines)


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
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("TCP_PROXY_WORKERS", "1")),
        help="Number of uvicorn worker processes (default: TCP_PROXY_WORKERS or 1)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Print the resolved pack-manifest state for the current workspace and exit",
    )
    parser.add_argument(
        "--doctor-format",
        choices=("text", "json"),
        default="text",
        help="Output format for --doctor (default: text)",
    )
    args = parser.parse_args()
    if args.doctor:
        try:
            inspection = inspect_pack_state(
                workspace_allowed_servers=_get_workspace_allowed_mcp_servers(),
            )
        except PackManifestError as exc:
            parser.exit(2, f"error: {exc}\n")
        if args.doctor_format == "json":
            print(json.dumps(_doctor_payload(inspection), indent=2, sort_keys=True))
            return
        print(_render_doctor_text(inspection))
        return
    if args.workers < 1:
        parser.exit(2, "error: --workers must be >= 1\n")
    import uvicorn

    app_target: str | Starlette = (
        "tcp.proxy.cc_proxy:app" if args.workers > 1 else build_app()
    )
    uvicorn.run(
        app_target,
        host=args.host,
        port=args.port,
        log_level="info",
        timeout_keep_alive=600,
        workers=args.workers,
    )


app = build_app()

if __name__ == "__main__":
    main()
