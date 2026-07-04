"""TCP-IMP-24: Stage 4.5 enforcement by deferred-schema demotion.

Merge bar:
  1. Core/safety-floor tools survive risky prompts with materialized schemas
     even under enforcement.
  2. Demotion never removes a tool name — the visible tool count is invariant.
  3. Abstained reductions are a strict no-op (surface identical to telemetry
     mode), so ambiguous prompts keep all alternatives.
  4. Non-MCP built-ins and already-deferred tools are never demoted.
  5. Demoted tools land in deferred_schema_tools — the surface CRG resolves as
     schema_deferred, never unavailable (false-denial safety by construction).
  6. The default flag value (telemetry) changes nothing; merging is inert.
  7. Conservation: every input tool has exactly one logged disposition.
  8. Genuine environment impossibilities still remove tools; demotion never
     resurrects a Stage-1-rejected tool.
  9. The counterfactual demotion-candidate set is logged in every enforcement
     mode (promotion-gate replay scores against it), and in demote mode the
     applied set equals the logged candidate set.
 10. A reducer-demoted tool resolves as schema_deferred in the CRG, never
     unavailable (the false-denial contract, asserted directly).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

import tcp.proxy.cc_proxy as cc
from tcp.proxy.capability_resolution_gate import (
    extract_requested_capabilities,
    resolve_capabilities_for_request,
)
from tcp.proxy.cc_proxy import (
    _SAFETY_FLOOR_TOOLS,
    _compute_reducer_shortlist_hit,
    _process_tools_array,
    _reducer_enforcement_mode,
)
from tcp.proxy.survivor_reducer import (
    ENFORCEMENT_VERSION,
    REDUCER_VERSION,
    SurvivorReduction,
    demotion_candidates,
)

# ── Env hygiene ───────────────────────────────────────────────────────────────
# _process_tools_array reads proxy env vars at call time; clear them so tests
# are hermetic regardless of the operator's live proxy configuration.

_PROXY_ENV_VARS = (
    "TCP_PROXY_REDUCER_ENFORCE",
    "TCP_PROXY_REDUCER_RECENCY_TTL",
    "TCP_PROXY_CWD",
    "TCP_PROXY_ALLOWED_MCP_SERVERS",
    "TCP_PROXY_WORKSPACE_MCP_SERVERS",
    "TCP_PROXY_NETWORK",
    "TCP_PROXY_FILE_ACCESS",
    "TCP_PROXY_STDIN",
    "TCP_PROXY_WORKSPACE_PROFILE",
    "TCP_PROXY_PROFILE",
    "TCP_PROXY_ENABLE_BAY_VIEW_GRAPH",
    "TCP_PROXY_PERMISSION_MODE",
)


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clean_recency_registry() -> None:
    """The recency shield is module-global in-memory state; isolate tests."""
    cc._recent_server_calls.clear()
    yield
    cc._recent_server_calls.clear()


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _tool(name: str, description: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description if description is not None else f"Tool {name}",
        "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }


def _body(text: str) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": text}]},
        ]
    }


# All MCP servers here are in DEFAULT_ACTIVE_MCP_SERVERS (state ACTIVE), so
# Stage 2 keeps everything and demotion is the only surface-shaping force.
TOOLS = [
    _tool("Read"),
    _tool("Bash"),
    _tool("Grep"),
    _tool("WebFetch"),  # non-MCP built-in, NOT in the safety floor
    _tool("mcp__git__git_status"),  # MCP member of the safety floor
    _tool("mcp__notion-agents__start_agent_run", "Run a Notion agent"),
    _tool("mcp__oracle-remote__execute_query", "Run a SQL query on Oracle"),
]

# "notion" gives the notion-agents tool CRG-family + lexical evidence; the
# oracle tool gets none, making it the demotion candidate.
EVIDENCE_BODY = _body("search my notion workspace for the meeting summary")
# No tool-name tokens, no CRG capability patterns → reducer abstains.
NO_EVIDENCE_BODY = _body("tidy up the changelog wording please")

ORACLE = "mcp__oracle-remote__execute_query"
NOTION = "mcp__notion-agents__start_agent_run"


def _names(tools: list[dict[str, Any]]) -> set[str]:
    return {t["name"] for t in tools}


def _by_name(tools: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(t for t in tools if t["name"] == name)


# ── 6. Default flag value is inert ───────────────────────────────────────────


class TestTelemetryDefault:
    def test_default_mode_is_telemetry(self) -> None:
        assert _reducer_enforcement_mode() == "telemetry"

    def test_invalid_flag_value_falls_back_to_telemetry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "remove-everything")
        assert _reducer_enforcement_mode() == "telemetry"

    def test_telemetry_mode_never_demotes(self) -> None:
        out, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live")
        assert meta["reducer_enforcement_mode"] == "telemetry"
        assert meta["reducer_demoted_tools"] == []
        assert meta["reducer_demoted_count"] == 0
        assert ORACLE in meta["materialized_schema_tools"]
        # Counterfactual shortlist is still logged for replay.
        assert meta["reducer_version"] == REDUCER_VERSION
        assert isinstance(meta["reducer_shortlisted_tools"], list)

    def test_telemetry_mode_logs_counterfactual_candidates(self) -> None:
        # Promotion gating replays called tools against this field; it must be
        # populated even when enforcement is off and never applied.
        out, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live")
        assert meta["reducer_enforcement_version"] == ENFORCEMENT_VERSION
        assert ORACLE in meta["reducer_demotion_candidates"]
        assert meta["reducer_demotion_candidate_count"] == len(
            meta["reducer_demotion_candidates"]
        )
        assert ORACLE in meta["materialized_schema_tools"]
        assert meta["reducer_demoted_tools"] == []


# ── 2 + 5. Demotion defers, never removes ────────────────────────────────────


class TestDemoteMode:
    def test_demote_defers_non_shortlisted_mcp_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        out, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live")
        assert not meta["reducer_abstained"]
        # The tool name is still visible — demotion is not removal.
        assert _names(out) == _names(TOOLS)
        assert ORACLE in meta["reducer_demoted_tools"]
        # Demoted tools join the deferred surface (CRG → schema_deferred).
        assert ORACLE in meta["deferred_schema_tools"]
        assert ORACLE not in meta["materialized_schema_tools"]
        assert meta["surface_state_by_tool"][ORACLE] == "deferred"
        demoted_surface = _by_name(out, ORACLE)
        assert demoted_surface["description"].startswith("Deferred schema for")
        assert "reducer_demotion" in demoted_surface["description"]
        assert demoted_surface["input_schema"]["properties"] == {}
        # Applied set equals the logged counterfactual candidate set.
        assert meta["reducer_demoted_tools"] == meta["reducer_demotion_candidates"]

    def test_evidence_tool_stays_materialized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        out, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live")
        assert NOTION in meta["materialized_schema_tools"]
        assert _by_name(out, NOTION)["description"] == "Run a Notion agent"

    def test_tool_count_is_invariant_under_demotion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        out, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live")
        assert len(out) == len(TOOLS)
        assert meta.get("live_empty_fallback") is None

    def test_demote_inactive_in_shadow_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        out, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "shadow")
        assert meta["reducer_demoted_tools"] == []
        assert len(out) == len(TOOLS)

    def test_demote_inactive_in_live_strict_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        _, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live-strict")
        assert meta["reducer_demoted_tools"] == []


# ── 1 + 4. Safety floor and built-ins are untouchable ────────────────────────


class TestFloorAndBuiltins:
    def test_floor_tools_materialized_under_risky_prompt_and_demotion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        risky = _body(
            "Fix the dangerous rm -rf deletion in the /v1/responses endpoint "
            "and curl the api to verify"
        )
        out, meta = _process_tools_array(TOOLS, risky, "live")
        for name in ("Read", "Bash", "Grep", "mcp__git__git_status"):
            assert name in meta["materialized_schema_tools"], name
            assert name not in meta["reducer_demoted_tools"], name

    def test_floor_mcp_tool_without_evidence_is_not_demoted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        _, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live")
        # git_status has no evidence in a Notion prompt but is floor-protected.
        assert "mcp__git__git_status" in meta["materialized_schema_tools"]
        assert "mcp__git__git_status" not in meta["reducer_demoted_tools"]

    def test_non_mcp_builtin_without_evidence_is_not_demoted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        out, meta = _process_tools_array(TOOLS, EVIDENCE_BODY, "live")
        # WebFetch is neither floor nor MCP — exempt from demotion.
        assert "WebFetch" in meta["materialized_schema_tools"]
        assert _by_name(out, "WebFetch")["description"] == "Tool WebFetch"


# ── 3. Abstention is a strict no-op ──────────────────────────────────────────


class TestAbstention:
    def test_abstained_reduction_is_byte_identical_to_telemetry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        out_demote, meta_demote = _process_tools_array(TOOLS, NO_EVIDENCE_BODY, "live")
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "telemetry")
        out_telemetry, _ = _process_tools_array(TOOLS, NO_EVIDENCE_BODY, "live")
        assert meta_demote["reducer_abstained"] is True
        assert meta_demote["reducer_demoted_tools"] == []
        assert meta_demote["reducer_demotion_candidates"] == []
        assert out_demote == out_telemetry


# ── 7. Conservation: every tool has exactly one disposition ──────────────────


class TestConservation:
    def test_every_input_tool_has_exactly_one_disposition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        # bay-view-graph is suppressed by the default manifest → server_filtered.
        tools = TOOLS + [_tool("mcp__bay-view-graph__list_emails")]
        out, meta = _process_tools_array(tools, EVIDENCE_BODY, "live")
        materialized = set(meta["materialized_schema_tools"])
        deferred = set(meta["deferred_schema_tools"])
        filtered = set(meta["server_filtered"])
        assert materialized | deferred | filtered == _names(tools)
        assert not materialized & deferred
        assert not materialized & filtered
        assert not deferred & filtered
        # Demotions are attributed and contained within the deferred surface.
        assert set(meta["reducer_demoted_tools"]) <= deferred

    def test_demotion_never_resurrects_filtered_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        tools = TOOLS + [_tool("mcp__bay-view-graph__list_emails")]
        out, meta = _process_tools_array(tools, EVIDENCE_BODY, "live")
        assert "mcp__bay-view-graph__list_emails" not in _names(out)
        assert "mcp__bay-view-graph__list_emails" not in meta["reducer_demoted_tools"]


# ── 8. Environment impossibility still wins ──────────────────────────────────


class TestEnvironmentImpossibility:
    def test_network_disabled_removal_survives_demote_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        monkeypatch.setenv("TCP_PROXY_NETWORK", "0")
        tools = [
            _tool("Read"),
            _tool("Bash"),
            _tool("network_probe", "Fetch the https://example.com api endpoint"),
        ]
        out, meta = _process_tools_array(tools, EVIDENCE_BODY, "live")
        # Description-derived SUPPORTS_NETWORK + network disabled → Stage 1
        # rejection; demotion operates on survivors only and cannot rescue it.
        assert "network_probe" not in _names(out)
        assert "network_probe" not in meta["reducer_demoted_tools"]


# ── demotion_candidates unit invariants ──────────────────────────────────────


def _reduction(
    shortlisted: tuple[str, ...],
    abstained: bool = False,
) -> SurvivorReduction:
    return SurvivorReduction(
        original_count=10,
        shortlisted_count=len(shortlisted),
        shortlisted_tools=shortlisted,
        ranked_tools=shortlisted,
        abstained=abstained,
        abstain_reason="no_positive_evidence" if abstained else None,
        reducer_version=REDUCER_VERSION,
    )


class TestDemotionCandidates:
    SURFACE = {
        "mcp__exa__web_search_exa": {"surface_state": "active", "mcp_server": "exa"},
        "mcp__git__git_status": {"surface_state": "active", "mcp_server": "git"},
        "mcp__bay-view-graph__get_email": {
            "surface_state": "deferred",
            "mcp_server": "bay-view-graph",
        },
        "Read": {"surface_state": "active", "mcp_server": None},
    }
    SURVIVORS = frozenset(SURFACE)

    def test_abstained_yields_empty_set(self) -> None:
        out = demotion_candidates(
            _reduction((), abstained=True),
            self.SURVIVORS,
            self.SURFACE,
            _SAFETY_FLOOR_TOOLS,
        )
        assert out == frozenset()

    def test_floor_non_mcp_deferred_and_shortlisted_are_excluded(self) -> None:
        out = demotion_candidates(
            _reduction(("mcp__notion-agents__start_agent_run",)),
            self.SURVIVORS | {"mcp__notion-agents__start_agent_run"},
            {
                **self.SURFACE,
                "mcp__notion-agents__start_agent_run": {
                    "surface_state": "active",
                    "mcp_server": "notion-agents",
                },
            },
            _SAFETY_FLOOR_TOOLS,
        )
        # Only the active, non-floor, non-shortlisted MCP tool is a candidate.
        assert out == frozenset({"mcp__exa__web_search_exa"})


# ── Recency shield: recent-server demotion protection ───────────────────────


class TestRecencyShield:
    """Enforcement v2: MCP servers called in the same workspace within the TTL
    are excluded from demotion candidates.  The shield narrows the candidate
    set only — it never alters the shortlist or the visible tool-name set."""

    WORKSPACE = "/ws/recency-itest"

    def _demote(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        monkeypatch.setenv("TCP_PROXY_REDUCER_ENFORCE", "demote")
        monkeypatch.setenv("TCP_PROXY_CWD", self.WORKSPACE)
        return _process_tools_array(TOOLS, EVIDENCE_BODY, "live")

    def test_recent_server_is_never_a_unit_candidate(self) -> None:
        surface = {
            **TestDemotionCandidates.SURFACE,
            NOTION: {"surface_state": "active", "mcp_server": "notion-agents"},
        }
        survivors = TestDemotionCandidates.SURVIVORS | {NOTION}
        base = demotion_candidates(
            _reduction((NOTION,)), survivors, surface, _SAFETY_FLOOR_TOOLS
        )
        assert base == frozenset({"mcp__exa__web_search_exa"})
        shielded = demotion_candidates(
            _reduction((NOTION,)),
            survivors,
            surface,
            _SAFETY_FLOOR_TOOLS,
            recent_mcp_servers=frozenset({"exa"}),
        )
        assert shielded == frozenset()

    def test_recent_oracle_call_shields_oracle_from_demotion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Baseline: no recent calls — oracle is demoted.
        out0, meta0 = self._demote(monkeypatch)
        assert ORACLE in meta0["reducer_demoted_tools"]
        assert meta0["reducer_recent_servers"] == []
        # A recent oracle call in the same workspace shields the server.
        cc._note_recent_tool_calls(self.WORKSPACE, [ORACLE], time.time())
        out1, meta1 = self._demote(monkeypatch)
        assert meta1["reducer_recent_servers"] == ["oracle-remote"]
        assert ORACLE not in meta1["reducer_demoted_tools"]
        assert ORACLE in meta1["materialized_schema_tools"]
        # Shield narrows candidates only: shortlist and name set unchanged.
        assert meta1["reducer_shortlisted_tools"] == meta0["reducer_shortlisted_tools"]
        assert _names(out1) == _names(TOOLS)
        assert meta1["reducer_recency_ttl_seconds"] == cc.RECENCY_TTL_DEFAULT_SECONDS

    def test_expired_recency_does_not_shield(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stale = time.time() - cc.RECENCY_TTL_DEFAULT_SECONDS - 60
        cc._note_recent_tool_calls(self.WORKSPACE, [ORACLE], stale)
        _, meta = self._demote(monkeypatch)
        assert meta["reducer_recent_servers"] == []
        assert ORACLE in meta["reducer_demoted_tools"]

    def test_shield_is_workspace_scoped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cc._note_recent_tool_calls("/ws/other-workspace", [ORACLE], time.time())
        _, meta = self._demote(monkeypatch)
        assert meta["reducer_recent_servers"] == []
        assert ORACLE in meta["reducer_demoted_tools"]

    def test_registry_ignores_non_mcp_names_and_empty_workspace(self) -> None:
        now = time.time()
        cc._note_recent_tool_calls(None, [ORACLE], now)
        cc._note_recent_tool_calls("", [ORACLE], now)
        cc._note_recent_tool_calls(self.WORKSPACE, ["Bash", "Read"], now)
        # No workspace entry is ever created for non-MCP-only call batches.
        assert cc._recent_server_calls == {}
        assert cc._recent_mcp_servers(None, now) == frozenset()
        assert cc._recent_mcp_servers(self.WORKSPACE, now) == frozenset()

    def test_ttl_env_override_and_fallbacks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_RECENCY_TTL", "60")
        assert cc._reducer_recency_ttl_seconds() == 60.0
        cc._note_recent_tool_calls(self.WORKSPACE, [ORACLE], time.time() - 120)
        assert cc._recent_mcp_servers(self.WORKSPACE, time.time()) == frozenset()
        # Invalid and negative values fail toward the bounded default.
        monkeypatch.setenv("TCP_PROXY_REDUCER_RECENCY_TTL", "forever")
        assert cc._reducer_recency_ttl_seconds() == cc.RECENCY_TTL_DEFAULT_SECONDS
        monkeypatch.setenv("TCP_PROXY_REDUCER_RECENCY_TTL", "-5")
        assert cc._reducer_recency_ttl_seconds() == cc.RECENCY_TTL_DEFAULT_SECONDS


# ── Startup registry warming ─────────────────────────────────────────────────


def _warm_row(ts: float, workspace: str, called: list[str]) -> dict[str, Any]:
    return {
        "ts": ts,
        "workspace_path": workspace,
        "reducer_version": REDUCER_VERSION,
        "tool_call_sequence": [{"tool_name": t} for t in called],
    }


def _write_log(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class TestRegistryWarming:
    """Startup warming seeds the recency registry from the tail of the decision
    log so the shield is not blind for one TTL window after a proxy restart.
    Warming only ADDS in-TTL entries — it never removes a tool or opens a
    denial the cold path wouldn't — and is best-effort (fails open)."""

    WS = "/ws/warm"
    NOW = 10_000.0
    EXA = "mcp__exa__web_search_exa"

    def test_seeds_in_ttl_server(self, tmp_path: Path) -> None:
        log = tmp_path / "decisions.jsonl"
        _write_log(log, [_warm_row(self.NOW - 100, self.WS, [ORACLE])])
        n = cc._warm_recency_registry_from_log(path=log, now=self.NOW)
        assert n == 1
        assert cc._recent_mcp_servers(self.WS, self.NOW) == frozenset({"oracle-remote"})

    def test_excludes_rows_older_than_ttl(self, tmp_path: Path) -> None:
        ttl = cc.RECENCY_TTL_DEFAULT_SECONDS
        log = tmp_path / "decisions.jsonl"
        # Append order: oldest first. The reverse reader must break at the
        # expired row without ever mis-seeding it, yet still seed the newer one.
        _write_log(
            log,
            [
                _warm_row(self.NOW - ttl - 60, self.WS, [ORACLE]),  # expired
                _warm_row(self.NOW - 30, self.WS, [NOTION]),  # in-window (newest)
            ],
        )
        cc._warm_recency_registry_from_log(path=log, now=self.NOW)
        assert cc._recent_mcp_servers(self.WS, self.NOW) == frozenset({"notion-agents"})

    def test_ignores_non_mcp_calls(self, tmp_path: Path) -> None:
        log = tmp_path / "decisions.jsonl"
        _write_log(log, [_warm_row(self.NOW - 10, self.WS, ["Bash", "Read"])])
        n = cc._warm_recency_registry_from_log(path=log, now=self.NOW)
        assert n == 0
        assert cc._recent_server_calls == {}

    def test_missing_log_fails_open(self, tmp_path: Path) -> None:
        n = cc._warm_recency_registry_from_log(
            path=tmp_path / "nope.jsonl", now=self.NOW
        )
        assert n == 0
        assert cc._recent_server_calls == {}

    def test_keeps_newest_ts_per_server(self, tmp_path: Path) -> None:
        ttl = cc.RECENCY_TTL_DEFAULT_SECONDS
        log = tmp_path / "decisions.jsonl"
        # Append order: oldest first, both within the window.
        _write_log(
            log,
            [
                _warm_row(self.NOW - ttl + 5, self.WS, [ORACLE]),  # older
                _warm_row(self.NOW - 5, self.WS, [ORACLE]),  # newest
            ],
        )
        cc._warm_recency_registry_from_log(path=log, now=self.NOW)
        # The stored ts is the newest call, so the server survives to now+ttl-5.
        assert cc._recent_mcp_servers(self.WS, self.NOW + ttl - 10) == frozenset(
            {"oracle-remote"}
        )

    def test_future_dated_row_ignored(self, tmp_path: Path) -> None:
        log = tmp_path / "decisions.jsonl"
        _write_log(log, [_warm_row(self.NOW + 500, self.WS, [ORACLE])])
        assert cc._warm_recency_registry_from_log(path=log, now=self.NOW) == 0

    def test_ttl_zero_disables_warming(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TCP_PROXY_REDUCER_RECENCY_TTL", "0")
        log = tmp_path / "decisions.jsonl"
        _write_log(log, [_warm_row(self.NOW - 5, self.WS, [ORACLE])])
        assert cc._warm_recency_registry_from_log(path=log, now=self.NOW) == 0
        assert cc._recent_server_calls == {}

    def test_reads_across_block_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Tiny blocks force the reverse reader to stitch lines across many
        # boundaries; all rows must still be recovered.
        monkeypatch.setattr(cc, "_WARM_BLOCK_BYTES", 8)
        log = tmp_path / "decisions.jsonl"
        _write_log(
            log,
            [
                _warm_row(self.NOW - 50, "/ws/a", [ORACLE]),
                _warm_row(self.NOW - 40, "/ws/b", [NOTION]),
                _warm_row(self.NOW - 30, "/ws/a", [self.EXA]),
            ],
        )
        cc._warm_recency_registry_from_log(path=log, now=self.NOW)
        assert cc._recent_mcp_servers("/ws/a", self.NOW) == frozenset(
            {"oracle-remote", "exa"}
        )
        assert cc._recent_mcp_servers("/ws/b", self.NOW) == frozenset({"notion-agents"})

    def test_warm_enabled_env_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TCP_PROXY_REDUCER_WARM", raising=False)
        assert cc._recency_warm_enabled() is True
        for off in ("0", "false", "No", "off"):
            monkeypatch.setenv("TCP_PROXY_REDUCER_WARM", off)
            assert cc._recency_warm_enabled() is False
        monkeypatch.setenv("TCP_PROXY_REDUCER_WARM", "1")
        assert cc._recency_warm_enabled() is True


# ── reducer_shortlist_hit helper ─────────────────────────────────────────────


class TestShortlistHit:
    META = {
        "reducer_abstained": False,
        "reducer_shortlisted_tools": ["Read", "mcp__git__git_status"],
    }

    def test_hit_and_miss(self) -> None:
        assert _compute_reducer_shortlist_hit(self.META, "Read") is True
        assert _compute_reducer_shortlist_hit(self.META, "Bash") is False

    def test_unscoreable_rows_are_none(self) -> None:
        assert _compute_reducer_shortlist_hit(self.META, None) is None
        abstained = {**self.META, "reducer_abstained": True}
        assert _compute_reducer_shortlist_hit(abstained, "Read") is None
        assert _compute_reducer_shortlist_hit({}, "Read") is None
        empty = {**self.META, "reducer_shortlisted_tools": []}
        assert _compute_reducer_shortlist_hit(empty, "Read") is None


# ── CRG precomputed-capabilities equivalence ─────────────────────────────────


def test_crg_precomputed_capabilities_match_self_extraction() -> None:
    prompt = "search my notion workspace for the meeting summary"
    kwargs: dict[str, Any] = dict(
        visible_tools=frozenset({NOTION}),
        deferred_tools=frozenset(),
        latent_tools=frozenset(),
        connector_servers=frozenset({"notion-agents"}),
        policy_blocked_tools=frozenset(),
        mode="live",
    )
    self_extracted = resolve_capabilities_for_request(prompt=prompt, **kwargs)
    precomputed = resolve_capabilities_for_request(
        prompt=prompt,
        capabilities=extract_requested_capabilities(prompt),
        **kwargs,
    )
    assert [(r.requested_capability, r.status) for r in self_extracted] == [
        (r.requested_capability, r.status) for r in precomputed
    ]
    assert self_extracted  # the prompt does imply capabilities


# ── 10. False-denial contract: demoted tools resolve schema_deferred ─────────


def test_crg_resolves_reducer_demoted_tool_as_schema_deferred() -> None:
    """A demoted tool joins the CRG deferred surface, so a capability backed
    only by that tool must resolve schema_deferred — never unavailable."""
    resolutions = resolve_capabilities_for_request(
        prompt="query the oracle database for the latest lab mirror rows",
        visible_tools=frozenset(),
        deferred_tools=frozenset({ORACLE}),  # reducer-demoted surface
        latent_tools=frozenset(),
        connector_servers=frozenset({"oracle-remote"}),
        policy_blocked_tools=frozenset(),
        mode="live",
    )
    oracle_res = [r for r in resolutions if r.requested_capability == "oracle.query"]
    assert oracle_res, "prompt must imply the oracle.query capability"
    for r in oracle_res:
        assert r.status == "schema_deferred"
        assert r.status != "unavailable"
