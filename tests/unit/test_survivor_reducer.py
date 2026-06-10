"""Unit tests for the evidence-gated Stage 4.5 survivor reducer (TCP-IMP-23)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcp.proxy.survivor_reducer import (
    ABSTAIN_NO_POSITIVE_EVIDENCE,
    ABSTAIN_NO_SURVIVORS,
    REDUCER_VERSION,
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
    SurvivorReduction,
    reduce_survivors,
)

_SAFETY_FLOOR = frozenset(
    {
        "Read",
        "Edit",
        "MultiEdit",
        "Write",
        "Glob",
        "Grep",
        "Bash",
        "Agent",
        "Skill",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
    }
)


def _make_surface(
    name: str,
    *,
    description: str = "",
    capability_flags: int = 0,
    state: str = STATE_ACTIVE,
) -> dict[str, object]:
    return {
        "description": description,
        "capability_flags": capability_flags,
        "surface_state": state,
    }


def _make_synthetic_survivor_set(
    n: int,
    *,
    extra: tuple[str, ...] = (),
) -> tuple[frozenset[str], dict[str, dict[str, object]]]:
    """Generate ``n`` synthetic MCP-style survivors plus optional extras."""
    survivors: set[str] = set()
    surface: dict[str, dict[str, object]] = {}
    for idx in range(n):
        nm = f"mcp__synth-server-{idx:03d}__do_thing"
        survivors.add(nm)
        surface[nm] = _make_surface(nm, description=f"synthetic tool {idx}")
    for nm in extra:
        survivors.add(nm)
        if nm not in surface:
            surface[nm] = _make_surface(nm)
    return frozenset(survivors), surface


# ── Test 1: modal 196-survivor case reduces to ≤20 with positive evidence ─────


def test_modal_196_survivor_set_reduces_to_at_most_15_with_evidence() -> None:
    survivors, surface = _make_synthetic_survivor_set(196)
    # Add a Notion-family tool that should match the CRG capability "notion.search".
    notion_tool = "mcp__notion-agents__notion-search"
    survivors = frozenset(survivors | {notion_tool})
    surface[notion_tool] = _make_surface(
        notion_tool, description="search notion workspace"
    )

    reduction = reduce_survivors(
        prompt="please search my notion workspace for the Q3 plan",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=["notion.search", "notion.read"],
        safety_floor_tools=_SAFETY_FLOOR,
    )

    assert reduction.original_count == 197
    assert not reduction.abstained
    # v2: shortlist is the evidence-ranked prefix only, capped at 15.
    assert len(reduction.shortlisted_tools) <= 15
    assert notion_tool in reduction.shortlisted_tools
    # The Notion tool, with both lexical + CRG hits, must rank ahead of any
    # synthetic tool with no evidence.
    assert reduction.ranked_tools[0] == notion_tool


# ── Test 2: 336-survivor case — shortlist is evidence-only, hard cap 15 ──────


def test_336_survivor_case_shortlist_is_evidence_only_capped_15() -> None:
    survivors, surface = _make_synthetic_survivor_set(
        336,
        extra=tuple(_SAFETY_FLOOR),
    )
    # Add a tool with positive evidence so the reducer does not abstain.
    notion_tool = "mcp__notion-agents__notion-search"
    survivors = frozenset(survivors | {notion_tool})
    surface[notion_tool] = _make_surface(notion_tool, description="search notion")

    reduction = reduce_survivors(
        prompt="check notion for the design doc",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
    )

    assert not reduction.abstained
    # v2: the floor is NOT unioned into the shortlist (replay showed the union
    # added zero protection and only inflated the size metric).  Floor tools
    # appear only if they earn evidence; this prompt gives them none.
    assert len(reduction.shortlisted_tools) <= 15
    assert not set(reduction.shortlisted_tools) & _SAFETY_FLOOR
    assert notion_tool in reduction.shortlisted_tools
    # The broad-shortlist failure mode is structurally impossible at the cap.
    assert reduction.shortlisted_count == len(reduction.shortlisted_tools)


# ── Test 3: floor protection lives at the demotion layer, not the shortlist ──


def test_safety_floor_survivors_protected_at_demotion_layer() -> None:
    from tcp.proxy.survivor_reducer import demotion_candidates

    survivors, surface = _make_synthetic_survivor_set(
        50,
        extra=("Read", "Bash", "Edit"),
    )
    # Single positive-evidence tool unrelated to the safety floor.
    notion_tool = "mcp__notion-agents__notion-search"
    survivors = frozenset(survivors | {notion_tool})
    surface[notion_tool] = _make_surface(notion_tool)

    reduction = reduce_survivors(
        prompt="search notion",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
    )

    assert not reduction.abstained
    # Evidence-less floor tools are not shortlisted in v2...
    assert "Read" not in reduction.shortlisted_tools
    # ...but they are never demotion candidates, which is the protection that
    # actually matters (shortlist membership had no enforcement effect).
    candidates = demotion_candidates(reduction, survivors, surface, _SAFETY_FLOOR)
    assert not candidates & _SAFETY_FLOOR
    # Non-MCP built-ins are structurally exempt as well.
    assert "Read" not in candidates and "Bash" not in candidates


# ── Test 4: no positive evidence → abstain ────────────────────────────────────


def test_no_positive_evidence_abstains() -> None:
    survivors, surface = _make_synthetic_survivor_set(50)
    reduction = reduce_survivors(
        prompt="hello world",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=[],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert reduction.abstained
    assert reduction.abstain_reason == ABSTAIN_NO_POSITIVE_EVIDENCE
    assert reduction.shortlisted_tools == ()
    assert reduction.shortlisted_count == 0
    assert reduction.original_count == 50
    # Ranking is still produced for telemetry, even when abstaining.
    assert len(reduction.ranked_tools) == 50


def test_empty_survivors_abstains_with_no_survivors_reason() -> None:
    reduction = reduce_survivors(
        prompt="any prompt",
        survivor_names=frozenset(),
        tool_surface_by_name={},
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=[],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert reduction.abstained
    assert reduction.abstain_reason == ABSTAIN_NO_SURVIVORS
    assert reduction.original_count == 0
    assert reduction.shortlisted_tools == ()


def test_feature_summary_schema_is_stable_across_paths() -> None:
    """Empty-survivors path emits the same keys as the populated path."""
    populated = reduce_survivors(
        prompt="search notion",
        survivor_names=frozenset({"mcp__notion-agents__notion-search"}),
        tool_surface_by_name={
            "mcp__notion-agents__notion-search": _make_surface(
                "mcp__notion-agents__notion-search"
            )
        },
        required_capability_flags=1,
        hard_capability_flags=2,
        heuristic_capability_flags=4,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
        max_shortlist=20,
    )
    empty = reduce_survivors(
        prompt="anything",
        survivor_names=frozenset(),
        tool_surface_by_name={},
        required_capability_flags=1,
        hard_capability_flags=2,
        heuristic_capability_flags=4,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
        max_shortlist=20,
    )
    assert set(populated.feature_summary.keys()) == set(empty.feature_summary.keys())


# ── Test 5: heuristic flags alone can rank but cannot hard-prune ──────────────


def test_heuristic_flags_alone_cannot_drive_emit() -> None:
    # Two survivors: one shares a heuristic flag bit, the other does not.
    # No exact name, no CRG family, no lexical match.  Reducer must abstain.
    survivors = frozenset({"mcp__alpha-server__some_action", "mcp__beta-server__other"})
    surface = {
        "mcp__alpha-server__some_action": _make_surface(
            "mcp__alpha-server__some_action", capability_flags=0b1010
        ),
        "mcp__beta-server__other": _make_surface(
            "mcp__beta-server__other", capability_flags=0b0001
        ),
    }
    reduction = reduce_survivors(
        prompt="generic request without any tool keywords",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0b1010,
        hard_capability_flags=0,
        heuristic_capability_flags=0b1010,
        crg_requested_capabilities=[],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert reduction.abstained, "heuristic flags alone must not yield emission"
    assert reduction.abstain_reason == ABSTAIN_NO_POSITIVE_EVIDENCE
    # But ranking still reflects the heuristic overlap.
    assert reduction.ranked_tools[0] == "mcp__alpha-server__some_action"


def test_heuristic_flags_can_break_ties_when_evidence_is_present() -> None:
    notion_tool = "mcp__notion-agents__notion-search"
    other_tool = "mcp__notion-agents__notion-write"
    survivors = frozenset({notion_tool, other_tool})
    surface = {
        notion_tool: _make_surface(notion_tool, capability_flags=0b1110),
        other_tool: _make_surface(other_tool, capability_flags=0b0001),
    }
    reduction = reduce_survivors(
        prompt="use notion to search",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0b1110,
        hard_capability_flags=0,
        heuristic_capability_flags=0b1110,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert not reduction.abstained
    assert reduction.ranked_tools[0] == notion_tool


# ── Token-boundary precision: substrings must not register as evidence ───────


def test_exact_name_requires_word_boundary_not_substring() -> None:
    """``git`` server name must not match inside ``digital`` (Codex review)."""
    git_tool = "mcp__git__git_status"
    survivors = frozenset({git_tool})
    surface = {git_tool: _make_surface(git_tool)}
    reduction = reduce_survivors(
        prompt="explain digital signatures",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=[],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert (
        reduction.abstained
    ), "substring of 'git' inside 'digital' must not register as evidence"
    assert reduction.abstain_reason == ABSTAIN_NO_POSITIVE_EVIDENCE


def test_exact_name_fires_on_word_bounded_mention() -> None:
    git_tool = "mcp__git__git_status"
    survivors = frozenset({git_tool})
    surface = {git_tool: _make_surface(git_tool)}
    reduction = reduce_survivors(
        prompt="run git status please",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=[],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert not reduction.abstained
    assert git_tool in reduction.shortlisted_tools


# ── Test 6: CRG family match outranks unrelated tools ─────────────────────────


def test_crg_family_match_ranks_above_unrelated() -> None:
    notion_tool = "mcp__notion-agents__notion-search"
    unrelated = "mcp__bay-view-graph__list_emails"
    survivors = frozenset({notion_tool, unrelated})
    surface = {
        notion_tool: _make_surface(notion_tool),
        unrelated: _make_surface(unrelated),
    }
    # Only the Notion CRG capability is requested.  A prompt that does not
    # mention either tool name puts ranking entirely on CRG family + state.
    reduction = reduce_survivors(
        prompt="do something abstract",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert reduction.ranked_tools[0] == notion_tool
    assert not reduction.abstained
    assert notion_tool in reduction.shortlisted_tools


# ── Determinism + structural invariants ───────────────────────────────────────


def test_reducer_output_is_deterministic() -> None:
    survivors, surface = _make_synthetic_survivor_set(80)
    notion_tool = "mcp__notion-agents__notion-search"
    survivors = frozenset(survivors | {notion_tool})
    surface[notion_tool] = _make_surface(notion_tool)

    args = dict(
        prompt="search notion",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    a = reduce_survivors(**args)
    b = reduce_survivors(**args)
    assert a == b


def test_reducer_version_string_is_stable() -> None:
    assert REDUCER_VERSION == "imp24.evidence_gated_reducer.v2"


def test_exact_name_mention_outranks_lexical_only_match() -> None:
    """Exact (whole tool/server name in prompt) must beat fuzzy token overlap."""
    exact_tool = "mcp__exa__web_search_exa"
    fuzzy_tool = "mcp__some-server__web_helper"  # shares only the 'web' token
    survivors = frozenset({exact_tool, fuzzy_tool})
    surface = {
        exact_tool: _make_surface(exact_tool),
        fuzzy_tool: _make_surface(fuzzy_tool),
    }
    reduction = reduce_survivors(
        prompt="use exa to search the web",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=[],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert not reduction.abstained
    assert reduction.ranked_tools[0] == exact_tool


def test_state_preference_active_outranks_deferred_when_other_signals_tie() -> None:
    notion_active = "mcp__notion-agents__notion-search"
    notion_deferred = "mcp__plugin_Notion_notion__notion-search"
    survivors = frozenset({notion_active, notion_deferred})
    surface = {
        notion_active: _make_surface(notion_active, state=STATE_ACTIVE),
        notion_deferred: _make_surface(notion_deferred, state=STATE_DEFERRED),
    }
    reduction = reduce_survivors(
        prompt="notion search",
        survivor_names=survivors,
        tool_surface_by_name=surface,
        required_capability_flags=0,
        hard_capability_flags=0,
        heuristic_capability_flags=0,
        crg_requested_capabilities=["notion.search"],
        safety_floor_tools=_SAFETY_FLOOR,
    )
    assert reduction.ranked_tools[0] == notion_active


# ── Replay-style fixture against decisions.jsonl when present ─────────────────


@pytest.mark.skipif(
    not (Path.home() / ".tcp-shadow" / "proxy" / "decisions.jsonl").exists(),
    reason="local decisions.jsonl not present in CI",
)
def test_replay_against_local_corpus_caps_broad_survivors() -> None:
    """When real corpus is available, broad-survivor rows with positive evidence
    must shortlist to ≤15 (evidence-only; no floor union in v2).

    This test is opportunistic: it is skipped in CI and on machines without a
    locally-running proxy.  It is meaningful on the operator's workstation.
    """
    path = Path.home() / ".tcp-shadow" / "proxy" / "decisions.jsonl"
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                rec.get("expected_tool_derivation_algorithm")
                != "imp22.evidence_gated.v1"
            ):
                continue
            sc = rec.get("survivor_count")
            if not isinstance(sc, int) or sc < 50:
                continue
            rows.append(rec)
    if not rows:
        pytest.skip("no broad-survivor post-IMP-22 rows in local corpus")

    # We do not have the original prompt text in decisions.jsonl (only its
    # excerpt) nor the full tool surface, so this fixture only verifies the
    # reducer's invariants on synthetic survivor sets sized to match the
    # observed buckets.  It is a structural sanity check, not a precision test.
    distinct_buckets = sorted({rec["survivor_count"] for rec in rows})[:5]
    for size in distinct_buckets:
        assert isinstance(size, int)
        survivors, surface = _make_synthetic_survivor_set(size)
        notion = "mcp__notion-agents__notion-search"
        survivors = frozenset(survivors | {notion})
        surface[notion] = _make_surface(notion)
        reduction = reduce_survivors(
            prompt="search notion",
            survivor_names=survivors,
            tool_surface_by_name=surface,
            required_capability_flags=0,
            hard_capability_flags=0,
            heuristic_capability_flags=0,
            crg_requested_capabilities=["notion.search"],
            safety_floor_tools=_SAFETY_FLOOR,
        )
        assert len(reduction.shortlisted_tools) <= 15, (
            f"reducer exceeded shortlist cap on survivor_count={size}: "
            f"count={len(reduction.shortlisted_tools)}"
        )


# ── Integration with cc_proxy: telemetry surfaces in decisions meta ──────────


def test_proxy_meta_emits_reducer_telemetry_in_shadow_mode() -> None:
    from tcp.proxy.cc_proxy import _process_tools_array  # local import

    tools = [
        {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
        {"name": "Bash", "description": "shell", "input_schema": {"type": "object"}},
        {
            "name": "mcp__notion-agents__notion-search",
            "description": "search notion",
            "input_schema": {"type": "object"},
        },
    ]
    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "search my notion workspace"}],
            }
        ],
    }
    _, meta = _process_tools_array(tools, body, "shadow")
    assert meta is not None
    assert meta.get("reducer_version") == REDUCER_VERSION
    assert isinstance(meta.get("reducer_original_count"), int)
    assert isinstance(meta.get("reducer_shortlisted_count"), int)
    assert isinstance(meta.get("reducer_shortlisted_tools"), list)
    assert isinstance(meta.get("reducer_ranked_tools"), list)
    assert isinstance(meta.get("reducer_abstained"), bool)
    assert "reducer_feature_summary" in meta
    # In shadow mode, the live tools list must be unchanged.
    assert meta["tool_count_after"] == len(tools)


def test_proxy_meta_reducer_does_not_alter_live_tools() -> None:
    from tcp.proxy.cc_proxy import _process_tools_array

    tools = [
        {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
        {
            "name": "mcp__notion-agents__notion-search",
            "description": "search notion",
            "input_schema": {"type": "object"},
        },
    ]
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "search notion"}]}
        ],
    }
    out_tools, meta = _process_tools_array(tools, body, "shadow")
    assert meta is not None
    assert len(out_tools) == len(tools)
    # Reducer telemetry is present, but the tools list is unchanged.
    assert meta.get("reducer_version") == REDUCER_VERSION


def test_dataclass_invariants() -> None:
    sr = SurvivorReduction(
        original_count=0,
        shortlisted_count=0,
        shortlisted_tools=(),
        ranked_tools=(),
        abstained=True,
        abstain_reason=ABSTAIN_NO_SURVIVORS,
        reducer_version=REDUCER_VERSION,
    )
    # SurvivorReduction is frozen — mutation must raise.
    with pytest.raises(Exception):
        sr.shortlisted_count = 5  # type: ignore[misc]
