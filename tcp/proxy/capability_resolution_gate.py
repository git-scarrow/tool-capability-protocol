"""Capability Resolution Gate (CRG) — TCP-DS-3.

Invariant: A capability denial may only be emitted after the resolver has
checked all six surfaces and returned status=unavailable.  Absence from
visible tools is never evidence of unavailability.

Six surfaces, checked in order but never short-circuited:
  1. visible          — active tools with full schema in the current context
  2. deferred         — tools with deferred/minimal schema (state=deferred)
  3. latent           — tools removed by server filtering (suppressed servers)
  4. connector        — all servers registered in the pack manifest (any state)
  5. policy           — tools gated by policy (live-strict gate decisions)
  6. schema_hydratable — tools whose schema can be loaded on demand

Status precedence:
  policy_blocked > callable_now > schema_deferred > approval_required > unavailable
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

# ── Signing ────────────────────────────────────────────────────────────────────
# Deterministic HMAC-SHA256 over resolution fields.  Prevents the model from
# authoring its own unavailable status — the enforcement layer verifies the
# signature before accepting any capability denial.

_CRG_RESOLVER_SECRET: str = os.environ.get(
    "TCP_CRG_RESOLVER_SECRET", "crg-resolver-default-v1"
)


def _compute_signature(
    *,
    resolver_id: str,
    requested_capability: str,
    status: str,
    matched_tools: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {
            "resolver_id": resolver_id,
            "requested_capability": requested_capability,
            "status": status,
            "matched_tools": sorted(matched_tools),
        },
        sort_keys=True,
    )
    return hmac.new(
        _CRG_RESOLVER_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


# ── Semantic capability → MCP server families ──────────────────────────────────
# Maps stable capability identifiers to the MCP server names that can satisfy them.
# This is the bootstrap latent descriptor index; a full index would be in a registry.

_CAPABILITY_SERVERS: dict[str, frozenset[str]] = {
    "notion.search": frozenset({"notion-agents", "plugin_Notion_notion"}),
    "notion.read": frozenset({"notion-agents", "plugin_Notion_notion"}),
    "notion.write": frozenset({"notion-agents", "plugin_Notion_notion"}),
    "github.code_search": frozenset({"github"}),
    "github.pr": frozenset({"github"}),
    "calendar.read": frozenset({"bay-view-graph"}),
    "calendar.write": frozenset({"bay-view-graph"}),
    "email.read": frozenset({"bay-view-graph"}),
    "email.send": frozenset({"bay-view-graph"}),
    "email.search": frozenset({"bay-view-graph"}),
    "oracle.query": frozenset({"oracle-remote"}),
    "web.fetch": frozenset({"fetch", "exa"}),
    "web.search": frozenset({"exa", "nixos", "fetch"}),
    "nix.package_search": frozenset({"nixos"}),
    "filesystem.read": frozenset({"filesystem"}),
    "filesystem.write": frozenset({"filesystem"}),
    "git.log": frozenset({"git"}),
    "git.diff": frozenset({"git"}),
    "chatsearch.find": frozenset({"chatsearch"}),
    "writing.rag": frozenset({"writing-rag"}),
    "claude_projects.read": frozenset({"claude-projects"}),
    "context7.docs": frozenset({"context7"}),
}

# All servers known to the capability index (union of all server families).
_ALL_KNOWN_SERVERS: frozenset[str] = frozenset().union(*_CAPABILITY_SERVERS.values())

# ── Prompt → semantic capability extraction ────────────────────────────────────

_NOTION_RE = re.compile(r"\b(notion|my workspace|notion database|notion page)\b", re.I)
_GITHUB_RE = re.compile(r"\b(github|pull.?request|pr\b|code.?search|repository)\b", re.I)
_CALENDAR_RE = re.compile(r"\b(calendar|meetings?|event|schedule|appointment)\b", re.I)
_EMAIL_RE = re.compile(r"\b(email|mail|inbox|send.?message|outlook)\b", re.I)
_ORACLE_RE = re.compile(r"\b(oracle|oracle.?db|sql.?query)\b", re.I)
_WEB_FETCH_RE = re.compile(r"\b(fetch.+url|curl|browse.+page|open.+url|http)\b", re.I)
_WEB_SEARCH_RE = re.compile(r"\b(search.+web|web.+search|google|exa.+search)\b", re.I)
_NIX_RE = re.compile(r"\b(nix|nixos|nixpkgs?|home.?manager|flake)\b", re.I)
_GIT_RE = re.compile(r"\b(git\s|git log|git diff|commit|branch|merge)\b", re.I)
_CHATSEARCH_RE = re.compile(r"\b(chat.?search|chat history|prior conversation|past session)\b", re.I)
_ORACLE_REMOTE_RE = re.compile(r"\b(oracle.?remote|oracle database|lab.?mirror)\b", re.I)

_PATTERN_CAPABILITIES: list[tuple[re.Pattern[str], list[str]]] = [
    (_NOTION_RE, ["notion.search", "notion.read"]),
    (_GITHUB_RE, ["github.code_search", "github.pr"]),
    (_CALENDAR_RE, ["calendar.read"]),
    (_EMAIL_RE, ["email.read", "email.search"]),
    (_ORACLE_REMOTE_RE, ["oracle.query"]),
    (_WEB_FETCH_RE, ["web.fetch"]),
    (_WEB_SEARCH_RE, ["web.search"]),
    (_NIX_RE, ["nix.package_search"]),
    (_GIT_RE, ["git.log", "git.diff"]),
    (_CHATSEARCH_RE, ["chatsearch.find"]),
]

_REQUIRED_SIX_SURFACES: tuple[str, ...] = (
    "visible",
    "deferred",
    "latent",
    "connector",
    "policy",
    "schema_hydratable",
)

CapabilityStatus = Literal[
    "callable_now",
    "schema_deferred",
    "approval_required",
    "policy_blocked",
    "unavailable",
]


@dataclass(frozen=True)
class SurfaceResult:
    surface: str
    matched: bool
    tools: tuple[str, ...]
    timestamp: str
    reason: str
    stale: bool = False


@dataclass(frozen=True)
class CapabilityResolution:
    """Resolver output for one requested capability.

    The model may read status.  Only the resolver authors status.
    The signature field is an HMAC over (resolver_id, capability, status,
    matched_tools) — the enforcement layer verifies it before accepting
    any unavailable claim as a valid negative proof.
    """
    requested_capability: str
    status: CapabilityStatus
    matched_tools: tuple[str, ...]
    checked_surfaces: tuple[str, ...]
    surface_results: tuple[SurfaceResult, ...]
    confidence: float
    reason: str
    resolver_id: str = "crg:v1"
    signature: str = ""  # set by resolve_capability(); empty means unsigned


@dataclass(frozen=True)
class CRGContext:
    """Snapshot of proxy-stage data needed for six-surface resolution."""
    # Surface 1: active tools with materialized schema
    visible_tools: frozenset[str]
    # Surface 2: tools with deferred/minimal schema
    deferred_tools: frozenset[str]
    # Surface 3: tools removed by server suppression/filtering
    latent_tools: frozenset[str]
    # Surface 4: servers registered in the pack manifest (any state)
    connector_servers: frozenset[str]
    # Surface 5: tools blocked by policy in live-strict gate
    policy_blocked_tools: frozenset[str]
    # Surface 6 is derived: latent_tools | deferred_tools (schema-hydratable)
    mode: str


def _extract_mcp_server_from_tool_name(tool_name: str) -> str | None:
    """Extract MCP server prefix from a tool name like mcp__notion-agents__..."""
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    return parts[1] if len(parts) >= 2 else None


def _tools_for_servers(
    tool_names: frozenset[str],
    capability_servers: frozenset[str],
) -> tuple[str, ...]:
    """Return the subset of tool_names whose MCP server is in capability_servers."""
    matches: list[str] = []
    for name in sorted(tool_names):
        server = _extract_mcp_server_from_tool_name(name)
        if server in capability_servers:
            matches.append(name)
    return tuple(matches)


def _servers_for_tools(tool_names: frozenset[str]) -> frozenset[str]:
    servers: set[str] = set()
    for name in tool_names:
        s = _extract_mcp_server_from_tool_name(name)
        if s:
            servers.add(s)
    return frozenset(servers)


def _resolve_surface_visible(
    capability: str,
    ctx: CRGContext,
    ts: str,
) -> SurfaceResult:
    cap_servers = _CAPABILITY_SERVERS.get(capability, frozenset())
    matched_tools = _tools_for_servers(ctx.visible_tools, cap_servers)
    matched = bool(matched_tools)
    return SurfaceResult(
        surface="visible",
        matched=matched,
        tools=matched_tools,
        timestamp=ts,
        reason=(
            f"found {len(matched_tools)} tool(s) in active visible surface"
            if matched
            else "no matching tool in visible surface"
        ),
    )


def _resolve_surface_deferred(
    capability: str,
    ctx: CRGContext,
    ts: str,
) -> SurfaceResult:
    cap_servers = _CAPABILITY_SERVERS.get(capability, frozenset())
    matched_tools = _tools_for_servers(ctx.deferred_tools, cap_servers)
    matched = bool(matched_tools)
    return SurfaceResult(
        surface="deferred",
        matched=matched,
        tools=matched_tools,
        timestamp=ts,
        reason=(
            f"found {len(matched_tools)} tool(s) with deferred schema"
            if matched
            else "no matching tool in deferred surface"
        ),
    )


def _resolve_surface_latent(
    capability: str,
    ctx: CRGContext,
    ts: str,
) -> SurfaceResult:
    cap_servers = _CAPABILITY_SERVERS.get(capability, frozenset())
    matched_tools = _tools_for_servers(ctx.latent_tools, cap_servers)
    matched = bool(matched_tools)
    return SurfaceResult(
        surface="latent",
        matched=matched,
        tools=matched_tools,
        timestamp=ts,
        reason=(
            f"found {len(matched_tools)} suppressed tool(s) in latent surface"
            if matched
            else "no matching tool in latent surface (server-filtered set)"
        ),
    )


def _resolve_surface_connector(
    capability: str,
    ctx: CRGContext,
    ts: str,
) -> SurfaceResult:
    cap_servers = _CAPABILITY_SERVERS.get(capability, frozenset())
    matched_servers = sorted(cap_servers & ctx.connector_servers)
    matched = bool(matched_servers)
    # Connector surface matches server names, not tool names.
    return SurfaceResult(
        surface="connector",
        matched=matched,
        tools=tuple(matched_servers),
        timestamp=ts,
        reason=(
            f"connector inventory contains server(s): {matched_servers}"
            if matched
            else "no matching server in connector inventory"
        ),
    )


def _resolve_surface_policy(
    capability: str,
    ctx: CRGContext,
    ts: str,
) -> SurfaceResult:
    cap_servers = _CAPABILITY_SERVERS.get(capability, frozenset())
    blocked = _tools_for_servers(ctx.policy_blocked_tools, cap_servers)
    # Policy matches if any tool for this capability is in the blocked set.
    matched = bool(blocked)
    return SurfaceResult(
        surface="policy",
        matched=matched,
        tools=blocked,
        timestamp=ts,
        reason=(
            f"{len(blocked)} tool(s) for this capability are policy-gated"
            if matched
            else "no policy blocks found for this capability"
        ),
    )


def _resolve_surface_schema_hydratable(
    capability: str,
    ctx: CRGContext,
    ts: str,
) -> SurfaceResult:
    cap_servers = _CAPABILITY_SERVERS.get(capability, frozenset())
    # Schema-hydratable = deferred ∪ latent (either can be hydrated if server promoted)
    hydratable = ctx.deferred_tools | ctx.latent_tools
    matched_tools = _tools_for_servers(hydratable, cap_servers)
    matched = bool(matched_tools)
    return SurfaceResult(
        surface="schema_hydratable",
        matched=matched,
        tools=matched_tools,
        timestamp=ts,
        reason=(
            f"found {len(matched_tools)} tool(s) that can have schema hydrated on demand"
            if matched
            else "no schema-hydratable tools found for this capability"
        ),
    )


def _classify_status(
    surfaces: dict[str, SurfaceResult],
) -> tuple[CapabilityStatus, str, float]:
    """Apply precedence rules to produce a final status, reason, and confidence.

    Precedence:
      policy_blocked > callable_now > schema_deferred > approval_required > unavailable
    """
    policy = surfaces["policy"]
    visible = surfaces["visible"]
    deferred = surfaces["deferred"]
    latent = surfaces["latent"]
    connector = surfaces["connector"]
    schema_hydratable = surfaces["schema_hydratable"]

    # policy_blocked wins globally only when the capability's tools are all blocked.
    # If visible surface also matched, callable_now takes precedence per spec:
    # "A blocked individual tool does not suppress another callable matching tool."
    if policy.matched and not visible.matched:
        return (
            "policy_blocked",
            f"capability blocked by policy; no callable alternative found",
            0.9,
        )

    if visible.matched:
        return (
            "callable_now",
            f"matched {len(visible.tools)} callable tool(s) in visible surface",
            1.0,
        )

    if deferred.matched or latent.matched or schema_hydratable.matched:
        matched_count = len(deferred.tools) + len(latent.tools)
        return (
            "schema_deferred",
            (
                f"matched {matched_count} tool(s) in deferred/latent surfaces; "
                "schema hydration or pack promotion required"
            ),
            0.8 if latent.matched else 0.9,
        )

    if connector.matched:
        return (
            "schema_deferred",
            f"server registered in connector inventory but no tool seen: {list(connector.tools)}",
            0.6,
        )

    return (
        "unavailable",
        "no matching capability found across all six checked surfaces",
        1.0,
    )


def resolve_capability(
    capability: str,
    ctx: CRGContext,
) -> CapabilityResolution:
    """Resolve a single semantic capability identifier against all six surfaces."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    surface_map: dict[str, SurfaceResult] = {
        "visible": _resolve_surface_visible(capability, ctx, ts),
        "deferred": _resolve_surface_deferred(capability, ctx, ts),
        "latent": _resolve_surface_latent(capability, ctx, ts),
        "connector": _resolve_surface_connector(capability, ctx, ts),
        "policy": _resolve_surface_policy(capability, ctx, ts),
        "schema_hydratable": _resolve_surface_schema_hydratable(capability, ctx, ts),
    }

    status, reason, confidence = _classify_status(surface_map)

    all_matched: set[str] = set()
    for sr in surface_map.values():
        all_matched.update(sr.tools)

    matched_tools = tuple(sorted(all_matched))
    resolver_id = "crg:v1"
    sig = _compute_signature(
        resolver_id=resolver_id,
        requested_capability=capability,
        status=status,
        matched_tools=matched_tools,
    )

    return CapabilityResolution(
        requested_capability=capability,
        status=status,
        matched_tools=matched_tools,
        checked_surfaces=_REQUIRED_SIX_SURFACES,
        surface_results=tuple(surface_map[s] for s in _REQUIRED_SIX_SURFACES),
        confidence=confidence,
        reason=reason,
        resolver_id=resolver_id,
        signature=sig,
    )


def extract_requested_capabilities(prompt: str) -> list[str]:
    """Extract semantic capability identifiers from user prompt text."""
    caps: list[str] = []
    for pattern, capability_ids in _PATTERN_CAPABILITIES:
        if pattern.search(prompt):
            for cap in capability_ids:
                if cap not in caps:
                    caps.append(cap)
    return caps


def resolve_capabilities_for_request(
    prompt: str,
    visible_tools: frozenset[str],
    deferred_tools: frozenset[str],
    latent_tools: frozenset[str],
    connector_servers: frozenset[str],
    policy_blocked_tools: frozenset[str],
    mode: str,
) -> list[CapabilityResolution]:
    """Resolve all capabilities implied by the prompt. Returns empty list when
    no semantic capabilities are detected (non-capability-seeking prompts)."""
    capabilities = extract_requested_capabilities(prompt)
    if not capabilities:
        return []

    ctx = CRGContext(
        visible_tools=visible_tools,
        deferred_tools=deferred_tools,
        latent_tools=latent_tools,
        connector_servers=connector_servers,
        policy_blocked_tools=policy_blocked_tools,
        mode=mode,
    )
    return [resolve_capability(cap, ctx) for cap in capabilities]


def resolution_to_log_record(resolution: CapabilityResolution) -> dict[str, Any]:
    """Serialize a CapabilityResolution to a decisions.jsonl record."""
    return {
        "kind": "capability_resolution",
        "requested_capability": resolution.requested_capability,
        "status": resolution.status,
        "matched_tools": list(resolution.matched_tools),
        "checked_surfaces": list(resolution.checked_surfaces),
        "surface_results": [
            {
                "surface": sr.surface,
                "matched": sr.matched,
                "tools": list(sr.tools),
                "timestamp": sr.timestamp,
                "reason": sr.reason,
                "stale": sr.stale,
            }
            for sr in resolution.surface_results
        ],
        "confidence": resolution.confidence,
        "reason": resolution.reason,
        "resolver_id": resolution.resolver_id,
        "signature": resolution.signature,
    }
