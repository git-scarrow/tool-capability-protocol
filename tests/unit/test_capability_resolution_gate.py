"""Unit tests for the Capability Resolution Gate (CRG).

Verification targets:
  V1 — Latent-only capability: suppressed server → schema_deferred, not unavailable
  V2 — Unknown capability: unavailable with all 6 surfaces checked
  V3 — Visible capability: callable_now
  V4 — Connector-only match (server in manifest but no tools seen): schema_deferred
  V5 — Policy-blocked without visible alternative: policy_blocked
  V6 — Six surfaces always present in every resolution record
  V7 — extract_requested_capabilities detects Notion, GitHub, calendar keywords
"""

from __future__ import annotations

import pytest

from tcp.proxy.capability_resolution_gate import (
    CRGContext,
    CapabilityResolution,
    _REQUIRED_SIX_SURFACES,
    extract_requested_capabilities,
    resolve_capability,
    resolve_capabilities_for_request,
    resolution_to_log_record,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _empty_ctx(**overrides) -> CRGContext:
    defaults = dict(
        visible_tools=frozenset(),
        deferred_tools=frozenset(),
        latent_tools=frozenset(),
        connector_servers=frozenset(),
        policy_blocked_tools=frozenset(),
        mode="live",
    )
    defaults.update(overrides)
    return CRGContext(**defaults)


def _notion_tool(n: int = 1) -> str:
    return f"mcp__notion-agents__query_database_{n}"


_NOTION_TOOL = "mcp__notion-agents__query_database"
_NOTION_SERVER = "notion-agents"


# ── V1: Latent-only capability ────────────────────────────────────────────────


class TestLatentOnlyCapability:
    """Server suppressed → tools in latent surface → schema_deferred, not unavailable."""

    def test_notion_in_latent_gives_schema_deferred(self):
        ctx = _empty_ctx(latent_tools=frozenset({_NOTION_TOOL}))
        r = resolve_capability("notion.search", ctx)
        assert (
            r.status == "schema_deferred"
        ), f"Expected schema_deferred but got {r.status}: {r.reason}"

    def test_notion_in_latent_matched_tools_populated(self):
        ctx = _empty_ctx(latent_tools=frozenset({_NOTION_TOOL}))
        r = resolve_capability("notion.search", ctx)
        assert _NOTION_TOOL in r.matched_tools

    def test_notion_in_latent_confidence_below_callable(self):
        ctx_latent = _empty_ctx(latent_tools=frozenset({_NOTION_TOOL}))
        ctx_visible = _empty_ctx(visible_tools=frozenset({_NOTION_TOOL}))
        r_latent = resolve_capability("notion.search", ctx_latent)
        r_visible = resolve_capability("notion.search", ctx_visible)
        assert r_latent.confidence < r_visible.confidence


# ── V2: Unknown capability → unavailable ─────────────────────────────────────


class TestUnavailableCapability:
    """No matching tools anywhere → unavailable with all six surfaces checked."""

    def test_unknown_capability_is_unavailable(self):
        ctx = _empty_ctx()
        r = resolve_capability("notion.search", ctx)
        assert r.status == "unavailable"

    def test_unavailable_lists_all_six_surfaces(self):
        ctx = _empty_ctx()
        r = resolve_capability("notion.search", ctx)
        assert set(r.checked_surfaces) == set(_REQUIRED_SIX_SURFACES)

    def test_unavailable_has_six_surface_results(self):
        ctx = _empty_ctx()
        r = resolve_capability("notion.search", ctx)
        assert len(r.surface_results) == 6

    def test_all_surfaces_unmatched_for_truly_unknown(self):
        ctx = _empty_ctx()
        r = resolve_capability("notion.search", ctx)
        for sr in r.surface_results:
            assert not sr.matched, f"Surface {sr.surface} unexpectedly matched"

    def test_unrecognized_capability_string_is_unavailable(self):
        ctx = _empty_ctx(visible_tools=frozenset({"mcp__notion-agents__foo"}))
        r = resolve_capability("totally.unknown.cap", ctx)
        assert r.status == "unavailable"


# ── V3: Visible capability → callable_now ────────────────────────────────────


class TestVisibleCapability:
    """Tool in visible surface → callable_now with confidence 1.0."""

    def test_visible_notion_tool_is_callable_now(self):
        ctx = _empty_ctx(visible_tools=frozenset({_NOTION_TOOL}))
        r = resolve_capability("notion.search", ctx)
        assert r.status == "callable_now"

    def test_callable_now_has_confidence_one(self):
        ctx = _empty_ctx(visible_tools=frozenset({_NOTION_TOOL}))
        r = resolve_capability("notion.search", ctx)
        assert r.confidence == 1.0

    def test_visible_overrides_latent(self):
        ctx = _empty_ctx(
            visible_tools=frozenset({_NOTION_TOOL}),
            latent_tools=frozenset({_NOTION_TOOL}),
        )
        r = resolve_capability("notion.search", ctx)
        assert r.status == "callable_now"


# ── V4: Connector-only match ──────────────────────────────────────────────────


class TestConnectorOnlyMatch:
    """Server in manifest but no tools seen → schema_deferred at lower confidence."""

    def test_connector_only_is_schema_deferred(self):
        ctx = _empty_ctx(connector_servers=frozenset({_NOTION_SERVER}))
        r = resolve_capability("notion.search", ctx)
        assert r.status == "schema_deferred"

    def test_connector_confidence_below_latent(self):
        ctx_connector = _empty_ctx(connector_servers=frozenset({_NOTION_SERVER}))
        ctx_latent = _empty_ctx(latent_tools=frozenset({_NOTION_TOOL}))
        r_conn = resolve_capability("notion.search", ctx_connector)
        r_lat = resolve_capability("notion.search", ctx_latent)
        assert r_conn.confidence <= r_lat.confidence


# ── V5: Policy-blocked without visible alternative ───────────────────────────


class TestPolicyBlocked:
    """Policy blocks a tool with no callable alternative → policy_blocked."""

    def test_policy_blocked_without_visible(self):
        ctx = _empty_ctx(policy_blocked_tools=frozenset({_NOTION_TOOL}))
        r = resolve_capability("notion.search", ctx)
        assert r.status == "policy_blocked"

    def test_visible_overrides_policy_block(self):
        """Per spec: a blocked individual tool doesn't suppress another callable tool."""
        ctx = _empty_ctx(
            visible_tools=frozenset({_NOTION_TOOL}),
            policy_blocked_tools=frozenset({_NOTION_TOOL}),
        )
        r = resolve_capability("notion.search", ctx)
        assert r.status == "callable_now"


# ── V6: Six surfaces always present ──────────────────────────────────────────


class TestSixSurfacesAlwaysPresent:
    """All six surface results must appear in every resolution."""

    @pytest.mark.parametrize(
        "status_scenario",
        [
            _empty_ctx(),  # unavailable
            _empty_ctx(visible_tools=frozenset({_NOTION_TOOL})),  # callable_now
            _empty_ctx(latent_tools=frozenset({_NOTION_TOOL})),  # schema_deferred
            _empty_ctx(
                connector_servers=frozenset({_NOTION_SERVER})
            ),  # schema_deferred
            _empty_ctx(
                policy_blocked_tools=frozenset({_NOTION_TOOL})
            ),  # policy_blocked
        ],
    )
    def test_all_six_surfaces_present(self, status_scenario):
        r = resolve_capability("notion.search", status_scenario)
        surface_names = {sr.surface for sr in r.surface_results}
        assert surface_names == set(
            _REQUIRED_SIX_SURFACES
        ), f"Missing surfaces: {set(_REQUIRED_SIX_SURFACES) - surface_names}"

    @pytest.mark.parametrize(
        "status_scenario",
        [
            _empty_ctx(),
            _empty_ctx(visible_tools=frozenset({_NOTION_TOOL})),
            _empty_ctx(latent_tools=frozenset({_NOTION_TOOL})),
        ],
    )
    def test_checked_surfaces_tuple_is_canonical(self, status_scenario):
        r = resolve_capability("notion.search", status_scenario)
        assert r.checked_surfaces == _REQUIRED_SIX_SURFACES


# ── V7: Capability extraction from prompt ────────────────────────────────────


class TestCapabilityExtraction:
    """extract_requested_capabilities detects semantic capabilities from prompts."""

    def test_notion_keyword_detected(self):
        caps = extract_requested_capabilities("search my Notion workspace for this doc")
        assert "notion.search" in caps

    def test_notion_database_detected(self):
        caps = extract_requested_capabilities("query the notion database")
        assert "notion.search" in caps

    def test_calendar_keyword_detected(self):
        caps = extract_requested_capabilities("what meetings do I have this week")
        assert "calendar.read" in caps

    def test_github_pr_detected(self):
        caps = extract_requested_capabilities("show me open pull requests on github")
        assert "github.pr" in caps or "github.code_search" in caps

    def test_nix_detected(self):
        caps = extract_requested_capabilities("is this package in nixpkgs unstable")
        assert "nix.package_search" in caps

    def test_no_false_positives_on_generic_prompt(self):
        caps = extract_requested_capabilities("write a Python function to sort a list")
        # Generic coding prompt should not trigger external connector capabilities.
        external = {"notion.search", "calendar.read", "email.read", "oracle.query"}
        assert not (set(caps) & external), f"False positives: {set(caps) & external}"

    def test_no_capabilities_returns_empty_resolution_list(self):
        resolutions = resolve_capabilities_for_request(
            prompt="write a sorting function",
            visible_tools=frozenset(),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        assert resolutions == []


# ── Log record serialization ──────────────────────────────────────────────────


class TestLogRecordSerialization:
    """resolution_to_log_record must produce a valid decisions.jsonl-compatible dict."""

    def test_kind_field_is_capability_resolution(self):
        ctx = _empty_ctx(visible_tools=frozenset({_NOTION_TOOL}))
        r = resolve_capability("notion.search", ctx)
        record = resolution_to_log_record(r)
        assert record["kind"] == "capability_resolution"

    def test_all_six_surfaces_in_surface_results_log(self):
        ctx = _empty_ctx()
        r = resolve_capability("notion.search", ctx)
        record = resolution_to_log_record(r)
        logged_surfaces = {sr["surface"] for sr in record["surface_results"]}
        assert logged_surfaces == set(_REQUIRED_SIX_SURFACES)

    def test_log_record_is_json_serializable(self):
        import json

        ctx = _empty_ctx(latent_tools=frozenset({_NOTION_TOOL}))
        r = resolve_capability("notion.search", ctx)
        record = resolution_to_log_record(r)
        json.dumps(record)  # must not raise

    def test_resolver_id_present(self):
        ctx = _empty_ctx()
        r = resolve_capability("notion.search", ctx)
        record = resolution_to_log_record(r)
        assert record["resolver_id"].startswith("crg:")
