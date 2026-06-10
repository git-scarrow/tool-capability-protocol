"""HTTP proxy: Claude Code → Anthropic with TCP gating (shadow or live).

Runtime defaults: file, network, and stdin are enabled unless you explicitly
disable them via TCP_PROXY_* env vars. A previous default of network_enabled=False
rejected every tool that carries SUPPORTS_NETWORK (including Bash), which breaks
Claude Code in live mode.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping, Sequence

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
from tcp.proxy.absence_language import (
    contains_absence_language,
    extract_text_from_response_body,
    extract_text_from_sse_buf,
)
from tcp.proxy.capability_resolution_gate import (
    CapabilityResolution,
    extract_requested_capabilities,
    resolution_to_log_record,
    resolve_capabilities_for_request,
)
from tcp.proxy.controller import (
    TPC_RULE_HEURISTIC_UPGRADE,
    ToolPackController,
    _server_alias_tokens,
)
from tcp.proxy.denial_enforcement import (
    denial_violation_record,
    enforce_denial_gate,
    may_emit_capability_denial,
)
from tcp.proxy.pack_manifest import (
    DEFAULT_ACTIVE_MCP_SERVERS,
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
    PackInspection,
    PackManifestError,
    default_manifest_path,
    inspect_pack_state,
    load_pack_manifest,
    pack_context_from_env,
)
from tcp.proxy.projection import ProjectionTier, project_single_anthropic_tool
from tcp.proxy.prompt_select import extract_task_prompt
from tcp.proxy.survivor_reducer import (
    ENFORCEMENT_VERSION,
    demotion_candidates,
    reduce_survivors,
)

PROXY_STATE_DIR = Path.home() / ".tcp-shadow" / "proxy"
MODE_PATH = PROXY_STATE_DIR / "mode"
DECISIONS_LOG = PROXY_STATE_DIR / "decisions.jsonl"

# Versioning: allows MT-21 analysis to distinguish rows logged before IMP-22
# (where absent expected_tool_* fields mean "not recorded") from rows logged
# after IMP-22 (where expected_tool_name=null means "system abstained").
DECISION_LOG_SCHEMA: int = 2
EXPECTED_TOOL_DERIVATION_ALGORITHM: str = "imp22.evidence_gated.v1"

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
DESC_SIM_MODE_ENV = "TCP_CC_DESC_SIM_MODE"
DESC_SIM_VALID_MODES = frozenset({"deferred", "off", "inline"})
DESC_SIM_DEFAULT_MODE = "deferred"
DESC_SIM_METHOD = "difflib_v1"
DESC_SIM_MAX_INLINE_PAIRS = int(
    os.environ.get("TCP_CC_DESC_SIM_MAX_INLINE_PAIRS", "2000")
)
DESC_SIM_MAX_INLINE_CHARS = int(
    os.environ.get("TCP_CC_DESC_SIM_MAX_INLINE_CHARS", "1024")
)
PROMPT_SIM_METHOD = "difflib_capped_v1"
PROMPT_SIM_MAX_PROMPT_CHARS = int(
    os.environ.get("TCP_CC_PROMPT_SIM_MAX_PROMPT_CHARS", "2048")
)
PROMPT_SIM_MAX_DESC_CHARS = int(
    os.environ.get("TCP_CC_PROMPT_SIM_MAX_DESC_CHARS", "1024")
)


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


# ── Stage 4.5 enforcement flag (TCP-IMP-24) ──────────────────────────────────
# "telemetry" (default): reducer output is logged only — byte-identical
# behavior to TCP-IMP-23.  "demote": in live mode, non-shortlisted, non-floor
# MCP survivors are sent with the deferred minimal-schema surface instead of a
# materialized schema.  Demotion never removes a tool name from the
# model-visible list, so demoted tools resolve as schema_deferred (never
# unavailable) in the Capability Resolution Gate.
# Unrecognized values fall back to "telemetry" (fail toward no enforcement).
REDUCER_ENFORCE_ENV = "TCP_PROXY_REDUCER_ENFORCE"
REDUCER_ENFORCE_TELEMETRY = "telemetry"
REDUCER_ENFORCE_DEMOTE = "demote"
_REDUCER_ENFORCE_VALID = frozenset({REDUCER_ENFORCE_TELEMETRY, REDUCER_ENFORCE_DEMOTE})


def _reducer_enforcement_mode() -> str:
    raw = os.environ.get(REDUCER_ENFORCE_ENV)
    if raw is None:
        return REDUCER_ENFORCE_TELEMETRY
    value = raw.strip().lower()
    return value if value in _REDUCER_ENFORCE_VALID else REDUCER_ENFORCE_TELEMETRY


# ── Stage 4.5 recency shield (enforcement v2) ────────────────────────────────
# Replay over decisions.jsonl showed that ~80% of would-be demotion misses are
# "context history" cases: the model re-calls a tool it used minutes earlier in
# the same workspace while the *current* prompt no longer names it.  Prompt
# text alone cannot recover that signal, so enforcement v2 shields the MCP
# servers observed handling tool calls in the same workspace within a decaying
# TTL window from demotion.  The shield narrows the demotion-candidate set
# only — it never alters the evidence shortlist or resurrects filtered tools.
# Audit/replay: the exact shielded-server set used for each decision is logged
# as reducer_recent_servers, and historical rows can reconstruct it from
# (workspace_path, ts, tool_call_sequence), which were already logged.
RECENCY_TTL_ENV = "TCP_PROXY_REDUCER_RECENCY_TTL"
RECENCY_TTL_DEFAULT_SECONDS = 1800.0

_recent_server_calls: dict[str, dict[str, float]] = {}


def _reducer_recency_ttl_seconds() -> float:
    raw = os.environ.get(RECENCY_TTL_ENV)
    if raw is None:
        return RECENCY_TTL_DEFAULT_SECONDS
    try:
        value = float(raw.strip())
    except ValueError:
        return RECENCY_TTL_DEFAULT_SECONDS
    # Fail toward no shield rather than an unbounded one.
    return value if value >= 0 else RECENCY_TTL_DEFAULT_SECONDS


def _note_recent_tool_calls(
    workspace: str | None, tool_names: Sequence[str], ts: float
) -> None:
    """Record observed MCP tool calls for the recency shield (in-memory)."""
    if not workspace:
        return
    for name in tool_names:
        server = _extract_mcp_server(name)
        if server is not None:
            servers = _recent_server_calls.setdefault(workspace, {})
            prev = servers.get(server)
            if prev is None or ts > prev:
                servers[server] = ts


def _recent_mcp_servers(workspace: str | None, now: float) -> frozenset[str]:
    """MCP servers called in this workspace within the recency TTL.

    Expired entries are pruned on lookup so the registry stays bounded by the
    number of distinct (workspace, server) pairs active within one TTL.
    """
    if not workspace:
        return frozenset()
    servers = _recent_server_calls.get(workspace)
    if not servers:
        return frozenset()
    ttl = _reducer_recency_ttl_seconds()
    expired = [s for s, last in servers.items() if now - last > ttl]
    for s in expired:
        del servers[s]
    if not servers:
        del _recent_server_calls[workspace]
        return frozenset()
    return frozenset(servers)


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


def _description_similarity_mode() -> str:
    mode = os.environ.get(DESC_SIM_MODE_ENV, DESC_SIM_DEFAULT_MODE).strip().lower()
    return mode if mode in DESC_SIM_VALID_MODES else DESC_SIM_DEFAULT_MODE


def _description_similarity_proxy_telemetry(
    tools: list[Any],
) -> dict[str, Any]:
    """Bound live description-similarity telemetry.

    The exact full-description metric is diagnostic only. It used to run in
    live preflight for every request, where large tool surfaces turned the
    O(n^2) SequenceMatcher loop into a seconds-long event-loop stall. Keep the
    legacy numeric field present, but make expensive exact computation explicit
    and opt-in.
    """
    descriptions = [
        t.get("description", "")
        for t in tools
        if isinstance(t, Mapping) and t.get("description")
    ]
    tool_count = len(descriptions)
    pair_count = tool_count * (tool_count - 1) // 2
    max_chars = max((len(str(d)) for d in descriptions), default=0)
    mode = _description_similarity_mode()

    base: dict[str, Any] = {
        "description_similarity_max": None,
        "description_similarity_max_status": mode,
        "description_similarity_max_method": DESC_SIM_METHOD,
        "description_similarity_max_pair_count": pair_count,
        "description_similarity_max_input_count": tool_count,
        "description_similarity_max_max_chars": max_chars,
    }

    if tool_count < 2:
        base["description_similarity_max"] = 0.0
        base["description_similarity_max_status"] = "exact"
        return base
    if mode == "off":
        base["description_similarity_max_status"] = "disabled"
        return base
    if mode == "deferred":
        return base
    if pair_count > DESC_SIM_MAX_INLINE_PAIRS or max_chars > DESC_SIM_MAX_INLINE_CHARS:
        base["description_similarity_max_status"] = "skipped_budget"
        return base

    base["description_similarity_max"] = _max_description_similarity_proxy(tools)
    base["description_similarity_max_status"] = "exact"
    return base


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
                if decision.tpc_rule == TPC_RULE_HEURISTIC_UPGRADE:
                    explicit_rescued.add(name)

    crg_policy_blocked: frozenset[str] = frozenset()

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
        crg_policy_blocked = frozenset(r.tool_name for r in strict_gate.rejected_tools)
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

    # ── Stage 4.5: Evidence-gated survivor reducer (TCP-IMP-23/24) ──────
    # Runs BEFORE the output build so enforcement (deferred-schema demotion)
    # can shape the schema surface.  Capability extraction happens exactly
    # once here; the same list is passed to CRG below so the reducer and the
    # resolver cannot disagree on the capability list.
    _requested_capabilities = extract_requested_capabilities(prompt or "")
    _tool_surface_for_reducer: dict[str, dict[str, Any]] = {}
    for _orig, _rec, _tier, _name in entries:
        if _rec is None or _rec.tool_name not in active_survivors:
            continue
        _r_server = _extract_mcp_server(_rec.tool_name)
        _r_decision = None if _r_server is None else controller_decisions.get(_r_server)
        _tool_surface_for_reducer[_rec.tool_name] = {
            "description": (
                _orig.get("description", "") if isinstance(_orig, Mapping) else ""
            ),
            "capability_flags": _rec.capability_flags,
            "surface_state": (
                STATE_ACTIVE if _r_decision is None else _r_decision.state
            ),
            "mcp_server": _r_server,
        }
    _reduction = reduce_survivors(
        prompt=prompt or "",
        survivor_names=frozenset(active_survivors),
        tool_surface_by_name=_tool_surface_for_reducer,
        required_capability_flags=tsel.required_capability_flags,
        hard_capability_flags=tsel.hard_capability_flags,
        heuristic_capability_flags=tsel.heuristic_capability_flags,
        crg_requested_capabilities=_requested_capabilities,
        safety_floor_tools=_SAFETY_FLOOR_TOOLS,
    )
    reducer_enforcement_mode = _reducer_enforcement_mode()
    # Candidates are computed in every mode so telemetry/shadow rows carry the
    # counterfactual "what live demote WOULD have stripped" — the promotion
    # gate for TCP-IMP-24 is scored against this field, not against hit rate
    # alone.  Application remains gated on live + demote.
    reducer_recent_servers = _recent_mcp_servers(session.cwd, time.time())
    reducer_demotion_candidate_set = demotion_candidates(
        _reduction,
        frozenset(active_survivors),
        _tool_surface_for_reducer,
        _SAFETY_FLOOR_TOOLS,
        recent_mcp_servers=reducer_recent_servers,
    )
    reducer_demoted: frozenset[str] = frozenset()
    if mode == "live" and reducer_enforcement_mode == REDUCER_ENFORCE_DEMOTE:
        reducer_demoted = reducer_demotion_candidate_set

    # ── Build output tool list ───────────────────────────────────────────
    live_tools: list[Any] = []
    materialized_schema_tools: list[str] = []
    deferred_schema_tools: list[str] = []
    reducer_demoted_applied: list[str] = []
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
        schema_state = STATE_ACTIVE if ctrl_decision is None else ctrl_decision.state
        # TCP-IMP-24: reducer demotion applies only to ACTIVE-surface tools, so
        # pack-state attribution for already-deferred tools is never clobbered.
        demoted_here = schema_state == STATE_ACTIVE and rec.tool_name in reducer_demoted
        if demoted_here:
            schema_state = STATE_DEFERRED
        surface_state_by_tool[rec.tool_name] = schema_state

        if mode in ("live", "live-strict") and schema_state == STATE_DEFERRED:
            live_tools.append(
                _deferred_tool_surface(
                    orig,
                    pack_id=(None if ctrl_decision is None else ctrl_decision.pack_id),
                    server=tool_server,
                    reason=(
                        "reducer_demotion"
                        if demoted_here
                        else (
                            server_allow_source.get(tool_server)
                            if tool_server
                            else None
                        )
                    ),
                )
            )
            deferred_schema_tools.append(rec.tool_name)
            if demoted_here:
                reducer_demoted_applied.append(rec.tool_name)
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
        **_description_similarity_proxy_telemetry(
            [
                orig
                for (orig, rec, _tier, _name) in entries
                if rec is not None and rec.tool_name in active_survivors
            ]
        ),
        # TCP-IMP-17: prompt-similarity ranking — top survivor regardless of count.
        # Populated when the task prompt is non-empty and any survivors exist.
        **_top_survivor_by_prompt_similarity_telemetry(
            prompt,
            [
                orig
                for (orig, rec, _tier, _name) in entries
                if rec is not None and rec.tool_name in active_survivors
            ],
        ),
    }

    # ── Stage 5: Capability Resolution Gate ──────────────────────────────
    # Resolves semantic capabilities implied by the prompt across all six
    # surfaces before the model context is finalized.  Adds crg_resolutions
    # to the decision log; never prunes or reorders the tool list.
    _crg_connector_servers: frozenset[str] = frozenset(
        s for pack in _PACK_MANIFEST.packs for s in pack.servers
    )
    _crg_resolutions = resolve_capabilities_for_request(
        prompt=prompt or "",
        visible_tools=frozenset(materialized_schema_tools),
        deferred_tools=frozenset(deferred_schema_tools),
        latent_tools=server_filtered,
        connector_servers=_crg_connector_servers,
        policy_blocked_tools=crg_policy_blocked,
        mode=mode,
        capabilities=_requested_capabilities,
    )
    if _crg_resolutions:
        meta["crg_resolution_count"] = len(_crg_resolutions)
        meta["crg_resolutions"] = [
            resolution_to_log_record(r) for r in _crg_resolutions
        ]
        # True when any resolved capability is not immediately callable.
        # A false-denial risk exists if the model cannot see the matched tools.
        meta["crg_false_denial_risk"] = any(
            r.status not in ("callable_now",) for r in _crg_resolutions
        )

    # ── Stage 4.5 telemetry (reduction computed before the output build) ──
    # Ranking never overrides the IMP-22 expected_tool_name derivation.
    # Enforcement (TCP-IMP-24) is demotion-only and recorded below.
    meta["reducer_version"] = _reduction.reducer_version
    meta["reducer_original_count"] = _reduction.original_count
    meta["reducer_shortlisted_count"] = _reduction.shortlisted_count
    meta["reducer_shortlisted_tools"] = list(_reduction.shortlisted_tools)
    meta["reducer_ranked_tools"] = list(_reduction.ranked_tools)
    meta["reducer_abstained"] = _reduction.abstained
    meta["reducer_abstain_reason"] = _reduction.abstain_reason
    meta["reducer_feature_summary"] = dict(_reduction.feature_summary)
    # TCP-IMP-24 enforcement attribution.  reducer_demotion_candidates is the
    # counterfactual set (logged in every mode for promotion-gate replay);
    # reducer_demoted_tools lists tools whose schema surface was actually
    # deferred BY THE REDUCER this request (disjoint from pack-state
    # deferrals, which keep their own server_allow_source).
    meta["reducer_enforcement_version"] = ENFORCEMENT_VERSION
    meta["reducer_enforcement_mode"] = reducer_enforcement_mode
    # Recency-shield audit trail: the exact server set that narrowed the
    # candidate set for THIS decision, so replay never has to re-derive it.
    meta["reducer_recent_servers"] = sorted(reducer_recent_servers)
    meta["reducer_recency_ttl_seconds"] = _reducer_recency_ttl_seconds()
    meta["reducer_demotion_candidate_count"] = len(reducer_demotion_candidate_set)
    meta["reducer_demotion_candidates"] = sorted(reducer_demotion_candidate_set)
    meta["reducer_demoted_count"] = len(reducer_demoted_applied)
    meta["reducer_demoted_tools"] = sorted(reducer_demoted_applied)

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


@dataclasses.dataclass(frozen=True)
class _ExpectedToolDerivation:
    """Evidence-gated derivation result for expected_tool_name.

    TCP-IMP-22: expected_tool_name is only emitted when supported by defensible
    evidence. All other cases abstain and set expected_tool_abstain_reason.
    """

    expected_tool_name: str | None
    derivation_source: str | None  # "single_survivor" | None
    candidate_set_size: int  # survivor_count at derivation time
    abstain_reason: str | None  # why expected_tool_name is None


def _top_survivor_by_prompt_similarity(
    prompt: str | None,
    tools: list[Any],
) -> str | None:
    """Return the survivor tool name with the highest prompt similarity.

    Computes SequenceMatcher ratio between the task prompt and each tool's
    ``name + description`` text, returning the argmax.  Returns None when
    prompt is empty/None or tools list is empty.

    DIAGNOSTIC ONLY (TCP-IMP-22): this field is stored in decisions.jsonl
    under ``top_survivor_by_similarity`` for analysis but must not drive
    expected_tool_name derivation.  See _compute_expected_tool_derivation.
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


def _top_survivor_by_prompt_similarity_telemetry(
    prompt: str | None,
    tools: list[Any],
) -> dict[str, Any]:
    """Bound prompt-to-tool similarity diagnostic work.

    Claude Code requests can include large system-reminder text in the extracted
    prompt. Running SequenceMatcher against full prompt and full tool
    descriptions makes this diagnostic scale with prompt size and tool-surface
    prose. Keep the legacy top_survivor_by_similarity field, but cap the text
    that feeds the diagnostic.
    """
    base: dict[str, Any] = {
        "top_survivor_by_similarity": None,
        "top_survivor_by_similarity_status": "empty",
        "top_survivor_by_similarity_method": PROMPT_SIM_METHOD,
        "top_survivor_by_similarity_prompt_chars": len(prompt or ""),
        "top_survivor_by_similarity_tool_count": len(tools),
    }
    if not prompt or not tools:
        return base

    prompt_capped = prompt[:PROMPT_SIM_MAX_PROMPT_CHARS].lower()
    capped = len(prompt) > PROMPT_SIM_MAX_PROMPT_CHARS
    best_name: str | None = None
    best_score = -1.0
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        name = tool.get("name", "") or ""
        desc = tool.get("description", "") or ""
        desc_s = desc if isinstance(desc, str) else str(desc)
        if len(desc_s) > PROMPT_SIM_MAX_DESC_CHARS:
            capped = True
            desc_s = desc_s[:PROMPT_SIM_MAX_DESC_CHARS]
        candidate = f"{name} {desc_s}".lower()
        score = difflib.SequenceMatcher(None, prompt_capped, candidate).ratio()
        if score > best_score:
            best_score = score
            best_name = name if isinstance(name, str) else None

    base["top_survivor_by_similarity"] = best_name
    base["top_survivor_by_similarity_status"] = "capped" if capped else "exact"
    return base


def _compute_expected_tool_name(meta: dict[str, Any] | None) -> _ExpectedToolDerivation:
    """Derive expected first tool with evidence gating (TCP-IMP-22).

    Emits expected_tool_name ONLY when supported by defensible evidence:
      - single_survivor: exactly one tool survived the gate (unambiguous).

    All other cases abstain with an explicit reason.  The prompt-similarity
    field (top_survivor_by_similarity) is preserved in the decision record as a
    diagnostic but is NOT used to derive expected_tool_name.

    Interpreting decisions.jsonl rows produced by this function:
      - expected_tool_name is non-null → single-survivor evidence; row is
        scoreable for first_tool_correct precision.
      - expected_tool_abstain_reason is non-null → abstained; exclude from
        precision/recall aggregation (the row has no ground-truth signal).
      - Rows produced before TCP-IMP-22 lack expected_tool_abstain_reason;
        treat their expected_tool_name as potentially noise-labeled.
    """
    if meta is None:
        return _ExpectedToolDerivation(
            expected_tool_name=None,
            derivation_source=None,
            candidate_set_size=0,
            abstain_reason="no_meta",
        )

    count = meta.get("survivor_count", 0)
    survivors = meta.get("survivor_names_sorted", [])

    # Single unambiguous survivor: the model has no choice; this is defensible.
    if count == 1 and len(survivors) == 1:
        return _ExpectedToolDerivation(
            expected_tool_name=survivors[0],
            derivation_source="single_survivor",
            candidate_set_size=1,
            abstain_reason=None,
        )

    # Defensive: count says 1 but list is empty or mismatched.
    if count == 1 and len(survivors) != 1:
        return _ExpectedToolDerivation(
            expected_tool_name=None,
            derivation_source=None,
            candidate_set_size=count,
            abstain_reason="count_list_mismatch",
        )

    if count == 0:
        reason = "no_survivors"
    else:
        reason = f"ambiguous_{count}_survivors"

    return _ExpectedToolDerivation(
        expected_tool_name=None,
        derivation_source=None,
        candidate_set_size=count,
        abstain_reason=reason,
    )


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


def _all_tools_from_sse_buf(buf: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Scan an SSE byte buffer for ALL tool_use content blocks.

    Returns ``(tools, stream_ended)`` where:
      - ``tools`` is an ordered list of ``{"index": int, "tool_name": str}`` dicts
        (one per tool_use content_block_start event, in observation order)
      - ``stream_ended`` is True when a ``message_stop`` event was seen

    Handles partial final lines gracefully — incomplete lines are skipped and
    will be re-presented in the next chunk via the caller's tail-buffer logic.
    """
    try:
        text = buf.decode("utf-8", errors="replace")
    except Exception:
        return [], False

    tools: list[dict[str, Any]] = []
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
                if isinstance(name, str):
                    tools.append({"index": data.get("index", 0), "tool_name": name})
        elif event_type == "message_stop":
            stream_ended = True

    return tools, stream_ended


def _all_tools_from_response_body(body: bytes) -> list[dict[str, Any]]:
    """Extract all tool_use blocks from a non-streamed Anthropic response.

    Returns an ordered list of ``{"index": int, "tool_name": str}`` dicts
    where ``index`` is the 0-based position in the response content array.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    result: list[dict[str, Any]] = []
    for idx, block in enumerate(data.get("content", [])):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str):
                result.append({"index": idx, "tool_name": name})
    return result


def _check_denial_enforcement(
    response_text: str,
    meta: dict[str, Any],
) -> None:
    """Run the denial gate against response text and stamp flat denial fields on meta.

    Reads crg_resolutions from meta (populated by Stage 5), runs
    may_emit_capability_denial, and:
      - Always sets flat denial_* fields on meta when absence-language is found.
      - Appends a denial_violation record to meta["denial_violations"] when the
        gate rejects (backward-compat list preserved alongside the flat fields).

    Decision-record flat fields added:
      denial_violation: bool
      denial_violation_reason: str | null
      denial_rewrite_action: str | null
      denial_matched_phrase: str | null
      denial_resolution_statuses: list[str]
    """
    if not response_text or not contains_absence_language(response_text):
        return
    crg_records = meta.get("crg_resolutions", [])
    # Reconstruct lightweight resolution stubs from logged records.
    # Full CapabilityResolution objects are not stored in meta — we use the
    # serialised records to check status, surfaces, and signature.
    from tcp.proxy.capability_resolution_gate import _REQUIRED_SIX_SURFACES as _SIX
    from tcp.proxy.capability_resolution_gate import SurfaceResult

    resolutions: list[CapabilityResolution] = []
    for rec in crg_records:
        surface_results = tuple(
            SurfaceResult(
                surface=sr["surface"],
                matched=sr["matched"],
                tools=tuple(sr.get("tools", [])),
                timestamp=sr.get("timestamp", ""),
                reason=sr.get("reason", ""),
                stale=sr.get("stale", False),
            )
            for sr in rec.get("surface_results", [])
        )
        resolutions.append(
            CapabilityResolution(
                requested_capability=rec.get("requested_capability", ""),
                status=rec.get("status", "unavailable"),
                matched_tools=tuple(rec.get("matched_tools", [])),
                checked_surfaces=tuple(rec.get("checked_surfaces", _SIX)),
                surface_results=surface_results,
                confidence=rec.get("confidence", 0.0),
                reason=rec.get("reason", ""),
                resolver_id=rec.get("resolver_id", "crg:v1"),
                signature=rec.get("signature", ""),
            )
        )

    decision = may_emit_capability_denial(response_text, resolutions)

    # Flat denial fields on the decisions.jsonl row (Phase 2A).
    meta["denial_violation"] = not decision.allowed
    meta["denial_violation_reason"] = decision.reason if not decision.allowed else None
    meta["denial_rewrite_action"] = decision.rewrite_action
    meta["denial_matched_phrase"] = decision.matched_absence_phrase
    meta["denial_resolution_statuses"] = [r.status for r in resolutions]

    if not decision.allowed:
        cap = resolutions[0].requested_capability if resolutions else None
        violation = denial_violation_record(decision, response_text, cap)
        existing = meta.get("denial_violations", [])
        existing.append(violation)
        meta["denial_violations"] = existing
        meta["denial_violation_count"] = len(existing)


def _compute_reducer_shortlist_hit(
    meta: Mapping[str, Any],
    first_tool_name: str | None,
) -> bool | None:
    """Scoreable shortlist accuracy for TCP-IMP-24 promotion gating.

    True/False only when a tool was actually called AND the reducer emitted a
    shortlist for this request.  None otherwise (no tool call, reducer
    abstained, or pre-IMP-24 row without reducer fields) — None rows must be
    excluded from hit-rate aggregation, mirroring the IMP-22 abstain contract.
    """
    if first_tool_name is None:
        return None
    if meta.get("reducer_abstained") is not False:
        return None
    shortlist = meta.get("reducer_shortlisted_tools")
    if not isinstance(shortlist, list) or not shortlist:
        return None
    return first_tool_name in set(shortlist)


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
    tool_call_sequence: list[dict[str, Any]] | None = None,
    stream_aborted: bool = False,
) -> None:
    """Write (or rewrite) the enriched decisions.jsonl entry for this turn."""
    # Feed the recency shield with the MCP calls observed this turn.  Uses
    # req_ts (the same ts written to the row) so offline reconstruction from
    # the log and the live registry agree.
    if tool_call_sequence:
        _note_recent_tool_calls(
            meta.get("workspace_path"),
            [
                c["tool_name"]
                for c in tool_call_sequence
                if isinstance(c, dict) and isinstance(c.get("tool_name"), str)
            ],
            req_ts,
        )
    derivation = _compute_expected_tool_name(meta)
    expected_tool_name = derivation.expected_tool_name
    first_tool_correct: bool | None = None
    if first_tool_name is not None and expected_tool_name is not None:
        first_tool_correct = first_tool_name == expected_tool_name

    _append_jsonl(
        DECISIONS_LOG,
        {
            "ts": req_ts,
            "path": "/v1/messages",
            **meta,
            "decision_log_schema": DECISION_LOG_SCHEMA,
            "first_tool_name": first_tool_name,
            "expected_tool_name": expected_tool_name,
            "expected_tool_derivation_source": derivation.derivation_source,
            "expected_tool_derivation_algorithm": EXPECTED_TOOL_DERIVATION_ALGORITHM,
            "expected_tool_candidate_set_size": derivation.candidate_set_size,
            "expected_tool_candidate_set_phase": "post_stage4_survivors",
            "expected_tool_abstain_reason": derivation.abstain_reason,
            "first_tool_correct": first_tool_correct,
            "reducer_shortlist_hit": _compute_reducer_shortlist_hit(
                meta, first_tool_name
            ),
            "tap_skipped": tap_skipped,
            "stream_aborted": stream_aborted,
            "preflight_duration_ms": preflight_duration_ms,
            "upstream_request_duration_ms": upstream_request_duration_ms,
            "first_byte_duration_ms": first_byte_duration_ms,
            "total_response_duration_ms": total_response_duration_ms,
            "retry_count": retry_count,
            "tool_call_sequence": tool_call_sequence,
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
    return isinstance(
        exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
    )


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
    if method.upper() in SAFE_RETRY_METHODS and isinstance(
        exc, (httpx.ReadError, httpx.ReadTimeout)
    ):
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
        can_tap = meta is not None and content_enc not in (
            "gzip",
            "br",
            "deflate",
            "zstd",
        )
        # State shared by body_iter closure.
        # _tap["buf"] holds only the unparsed tail (incomplete last line) so that
        # _all_tools_from_sse_buf never rescans already-consumed bytes (O(n) total).
        # _tap["tool_sequence"] accumulates all tool calls until message_stop.
        _tap: dict[str, Any] = {
            "buf": b"",
            "done": False,
            "tool_sequence": [],
            "text_buf": b"",
        }
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
                        _tap["text_buf"] = _tap["text_buf"] + chunk
                        new_tools, ended = _all_tools_from_sse_buf(combined)
                        # Assign seq numbers continuing from previous chunks.
                        seq_offset = len(_tap["tool_sequence"])
                        for i, t in enumerate(new_tools):
                            _tap["tool_sequence"].append(
                                {
                                    "seq": seq_offset + i,
                                    "index": t["index"],
                                    "tool_name": t["tool_name"],
                                }
                            )
                        if ended:
                            # message_stop seen — run denial enforcement then write decision.
                            assert meta is not None
                            response_text = extract_text_from_sse_buf(_tap["text_buf"])
                            _check_denial_enforcement(response_text, meta)
                            seq = _tap["tool_sequence"]
                            first_name = seq[0]["tool_name"] if seq else None
                            _write_decision_record(
                                req_ts,
                                meta,
                                first_name,
                                tap_skipped=False,
                                preflight_duration_ms=(preflight_done_at - started_at)
                                * 1000.0,
                                upstream_request_duration_ms=(
                                    (upstream_request_done_at - upstream_started_at)
                                    * 1000.0
                                    if upstream_request_done_at is not None
                                    else None
                                ),
                                first_byte_duration_ms=(
                                    (first_byte_at - started_at) * 1000.0
                                    if first_byte_at is not None
                                    else None
                                ),
                                total_response_duration_ms=(
                                    time.perf_counter() - started_at
                                )
                                * 1000.0,
                                retry_count=retry_count,
                                tool_call_sequence=seq if seq else [],
                            )
                            _tap["done"] = True
                            _tap["buf"] = b""  # release buffer memory
                        else:
                            # Retain only bytes after the last newline so the
                            # next chunk completes any split line without
                            # re-scanning already-parsed data (keeps O(n) total).
                            last_nl = combined.rfind(b"\n")
                            _tap["buf"] = (
                                combined[last_nl + 1 :] if last_nl >= 0 else combined
                            )
            finally:
                if can_tap and not _tap["done"]:
                    # Stream ended without a message_stop event
                    # (e.g. non-200, network error, client disconnect).
                    # Run denial enforcement on whatever text was accumulated
                    # before terminating, then write the decision record.
                    assert meta is not None
                    response_text = extract_text_from_sse_buf(_tap["text_buf"])
                    _check_denial_enforcement(response_text, meta)
                    seq = _tap["tool_sequence"]
                    first_name = seq[0]["tool_name"] if seq else None
                    _write_decision_record(
                        req_ts,
                        meta,
                        first_name,
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
                        total_response_duration_ms=(time.perf_counter() - started_at)
                        * 1000.0,
                        retry_count=retry_count,
                        tool_call_sequence=seq if seq else None,
                        stream_aborted=True,
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
                        total_response_duration_ms=(time.perf_counter() - started_at)
                        * 1000.0,
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
        # Non-streaming: extract all tool calls and run denial enforcement.
        if meta is not None:
            response_text = extract_text_from_response_body(content)
            _check_denial_enforcement(response_text, meta)
            all_tools = _all_tools_from_response_body(content)
            seq = [
                {"seq": i, "index": t["index"], "tool_name": t["tool_name"]}
                for i, t in enumerate(all_tools)
            ]
            first_tool: str | None = seq[0]["tool_name"] if seq else None
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
                tool_call_sequence=seq,
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
            Route(
                "/{path:path}",
                proxy_pass_through,
                methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
            ),
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
