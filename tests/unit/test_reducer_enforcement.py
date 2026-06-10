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

from typing import Any

import pytest

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
    SurvivorReduction,
    REDUCER_VERSION,
    demotion_candidates,
)

# ── Env hygiene ───────────────────────────────────────────────────────────────
# _process_tools_array reads proxy env vars at call time; clear them so tests
# are hermetic regardless of the operator's live proxy configuration.

_PROXY_ENV_VARS = (
    "TCP_PROXY_REDUCER_ENFORCE",
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
