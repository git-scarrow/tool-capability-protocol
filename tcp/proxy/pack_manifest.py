"""Deterministic pack manifest loading for proxy-stage family activation."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml


PackState = Literal["active", "deferred", "suppressed"]

MANIFEST_VERSION = 1
DEFAULT_PROFILE = "default"
STATE_ACTIVE: PackState = "active"
STATE_DEFERRED: PackState = "deferred"
STATE_SUPPRESSED: PackState = "suppressed"
VALID_PACK_STATES = frozenset({STATE_ACTIVE, STATE_DEFERRED, STATE_SUPPRESSED})

DEFAULT_ACTIVE_MCP_SERVERS: frozenset[str] = frozenset(
    {
        "filesystem",
        "git",
        "fetch",
        "context7",
        "exa",
        "nixos",
        "chatsearch",
        "writing-rag",
        "claude-projects",
        "notion-agents",
        "oracle-remote",
    }
)


@dataclass(frozen=True)
class PackRule:
    pack_id: str
    servers: frozenset[str]
    default_state: PackState
    allow_workspace: bool = False
    active_workspaces: frozenset[str] = frozenset()
    active_profiles: frozenset[str] = frozenset()
    active_env: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class PackManifest:
    version: int
    source_path: str
    packs: tuple[PackRule, ...]


@dataclass(frozen=True)
class PackContext:
    workspace_name: str
    workspace_path: str
    profile: str
    workspace_allowed_servers: frozenset[str]
    env: Mapping[str, str]


@dataclass(frozen=True)
class PackDecision:
    pack_id: str
    state: PackState
    reasons: tuple[str, ...]
    servers: tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_manifest_path() -> Path:
    return _repo_root() / ".tcp-proxy-packs.yaml"


def _default_manifest_data() -> dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "packs": [
            {
                "pack_id": "core-coding",
                "default_state": STATE_ACTIVE,
                "servers": sorted(DEFAULT_ACTIVE_MCP_SERVERS),
            },
            {
                "pack_id": "workspace-critical",
                "default_state": STATE_SUPPRESSED,
                "allow_workspace": True,
                "servers": ["bay-view-graph"],
                "activation": {
                    "profiles": ["bay-view"],
                    "workspaces": ["bay-view"],
                    "env": {
                        "TCP_PROXY_ENABLE_BAY_VIEW_GRAPH": [
                            "1",
                            "true",
                            "yes",
                            "on",
                        ],
                    },
                },
            },
        ],
    }


def _candidate_manifest_paths() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("TCP_PROXY_PACK_MANIFEST")
    if explicit:
        candidates.append(Path(explicit).expanduser())

    cwd_env = os.environ.get("TCP_PROXY_CWD")
    if cwd_env:
        candidates.append(Path(cwd_env).expanduser() / ".tcp-proxy-packs.yaml")

    candidates.append(Path.cwd() / ".tcp-proxy-packs.yaml")
    candidates.append(default_manifest_path())

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _as_str_set(raw: Any, *, field_name: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list")
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} entries must be non-empty strings")
        values.add(item.strip())
    return frozenset(values)


def _normalize_state(raw: Any, *, field_name: str) -> PackState:
    if not isinstance(raw, str):
        raise ValueError(f"{field_name} must be a string")
    state = raw.strip().lower()
    if state not in VALID_PACK_STATES:
        raise ValueError(f"{field_name} must be one of {sorted(VALID_PACK_STATES)}")
    return state  # type: ignore[return-value]


def _parse_active_env(raw: Any) -> dict[str, frozenset[str]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("activation.env must be a mapping")
    out: dict[str, frozenset[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("activation.env keys must be non-empty strings")
        if value is None:
            out[key.strip()] = frozenset()
            continue
        if not isinstance(value, list):
            raise ValueError("activation.env values must be lists of strings")
        normalized: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("activation.env values must be non-empty strings")
            normalized.add(item.strip().lower())
        out[key.strip()] = frozenset(normalized)
    return out


def _parse_pack_rule(raw: Any) -> PackRule:
    if not isinstance(raw, Mapping):
        raise ValueError("pack entries must be mappings")

    pack_id = raw.get("pack_id")
    if not isinstance(pack_id, str) or not pack_id.strip():
        raise ValueError("pack_id must be a non-empty string")

    activation = raw.get("activation") or {}
    if not isinstance(activation, Mapping):
        raise ValueError("activation must be a mapping")

    allow_workspace = raw.get("allow_workspace", False)
    if not isinstance(allow_workspace, bool):
        raise ValueError("allow_workspace must be a boolean")

    return PackRule(
        pack_id=pack_id.strip(),
        servers=_as_str_set(raw.get("servers"), field_name=f"{pack_id}.servers"),
        default_state=_normalize_state(
            raw.get("default_state", STATE_SUPPRESSED),
            field_name=f"{pack_id}.default_state",
        ),
        allow_workspace=allow_workspace,
        active_workspaces=_as_str_set(
            activation.get("workspaces"),
            field_name=f"{pack_id}.activation.workspaces",
        ),
        active_profiles=_as_str_set(
            activation.get("profiles"),
            field_name=f"{pack_id}.activation.profiles",
        ),
        active_env=_parse_active_env(activation.get("env")),
    )


def _build_manifest(data: Any, *, source_path: str) -> PackManifest:
    if not isinstance(data, Mapping):
        raise ValueError("pack manifest root must be a mapping")

    version = data.get("version", MANIFEST_VERSION)
    if version != MANIFEST_VERSION:
        raise ValueError(f"pack manifest version must be {MANIFEST_VERSION}")

    raw_packs = data.get("packs")
    if not isinstance(raw_packs, list) or not raw_packs:
        raise ValueError("pack manifest must contain a non-empty packs list")

    packs = tuple(_parse_pack_rule(item) for item in raw_packs)
    seen_pack_ids: set[str] = set()
    seen_servers: dict[str, str] = {}
    for pack in packs:
        if pack.pack_id in seen_pack_ids:
            raise ValueError(f"duplicate pack_id: {pack.pack_id}")
        seen_pack_ids.add(pack.pack_id)
        if not pack.servers:
            raise ValueError(f"{pack.pack_id}.servers must not be empty")
        for server in pack.servers:
            previous = seen_servers.get(server)
            if previous is not None:
                raise ValueError(
                    f"server {server!r} appears in both {previous!r} and {pack.pack_id!r}"
                )
            seen_servers[server] = pack.pack_id

    return PackManifest(version=MANIFEST_VERSION, source_path=source_path, packs=packs)


@lru_cache(maxsize=16)
def _load_manifest_from_cache_key(
    source_key: str,
    source_mtime_ns: int,
) -> PackManifest:
    if source_key == "<embedded-default>":
        return _build_manifest(_default_manifest_data(), source_path=source_key)
    path = Path(source_key)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _build_manifest(raw, source_path=source_key)


def load_pack_manifest(*, use_cache: bool = True) -> PackManifest:
    for path in _candidate_manifest_paths():
        if not path.exists():
            continue
        try:
            stat = path.stat()
            if use_cache:
                return _load_manifest_from_cache_key(str(path), stat.st_mtime_ns)
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            return _build_manifest(raw, source_path=str(path))
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return _load_manifest_from_cache_key("<embedded-default>", 0)


def pack_context_from_env(
    *,
    cwd: str | None = None,
    profile: str | None = None,
    workspace_allowed_servers: frozenset[str] | None = None,
) -> PackContext:
    workspace_path = str(Path(cwd or os.environ.get("TCP_PROXY_CWD") or os.getcwd()).resolve())
    workspace_name = Path(workspace_path).name
    resolved_profile = (
        profile
        or os.environ.get("TCP_PROXY_WORKSPACE_PROFILE")
        or os.environ.get("TCP_PROXY_PROFILE")
        or DEFAULT_PROFILE
    )
    return PackContext(
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        profile=resolved_profile,
        workspace_allowed_servers=workspace_allowed_servers or frozenset(),
        env=dict(os.environ),
    )


def _env_matches(expected: frozenset[str], raw: str | None) -> bool:
    if raw is None:
        return False
    if not expected:
        return True
    return raw.strip().lower() in expected


def resolve_pack_decisions(
    manifest: PackManifest,
    context: PackContext,
) -> tuple[dict[str, PackDecision], dict[str, PackDecision]]:
    pack_decisions: dict[str, PackDecision] = {}
    server_decisions: dict[str, PackDecision] = {}

    for pack in manifest.packs:
        reasons: list[str] = [f"default:{pack.default_state}"]
        state = pack.default_state

        workspace_match = (
            context.workspace_name in pack.active_workspaces
            or context.workspace_path in pack.active_workspaces
        )
        if workspace_match:
            state = STATE_ACTIVE
            reasons.append(f"workspace:{context.workspace_name}")

        if context.profile in pack.active_profiles:
            state = STATE_ACTIVE
            reasons.append(f"profile:{context.profile}")

        matched_env: list[str] = []
        for key, expected in pack.active_env.items():
            raw = context.env.get(key)
            if _env_matches(expected, raw):
                matched_env.append(f"{key}={raw}")
        if matched_env:
            state = STATE_ACTIVE
            reasons.extend(f"env:{item}" for item in matched_env)

        if (
            state != STATE_ACTIVE
            and pack.allow_workspace
            and (pack.servers & context.workspace_allowed_servers)
        ):
            state = STATE_DEFERRED
            reasons.append("workspace_allow")

        decision = PackDecision(
            pack_id=pack.pack_id,
            state=state,
            reasons=tuple(reasons),
            servers=tuple(sorted(pack.servers)),
        )
        pack_decisions[pack.pack_id] = decision
        for server in pack.servers:
            server_decisions[server] = decision

    return pack_decisions, server_decisions
