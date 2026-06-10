"""Promotion-gate replay script contract (scripts/replay_reducer_demotion.py).

The replay gate is the evidence that decides shadow-demote → live-demote
promotion, so its scoring rules are pinned here:

  1. The gate FAILS when called tools land in the demotion-candidate set even
     when shortlist hit rates look high — miss rate, not hit rate, gates.
  2. The gate PASSES only with >= 2000 scoreable rows, miss < 1% and median
     shortlist <= 15.
  3. Unscoreable rows (abstained, empty shortlist, no tool call) are excluded
     from rates but counted and reported — never silently dropped.
  4. --since windows scoring, but pre-window rows still feed the recency
     reconstruction so the shield is causally correct at the boundary.
  5. Broad logged shortlists (v1 rows, cap 20 + floor union) are re-capped to
     the v2 evidence prefix (<= 15), so the reported median is honest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "replay_reducer_demotion.py"

NOTION = "mcp__notion-agents__query_database"
EXA = "mcp__exa__web_search_exa"
ORACLE = "mcp__oracle-remote__execute_query"


def _row(
    ts: float,
    *,
    workspace: str = "/ws/replay",
    shortlist: list[str] | None = None,
    n_evidence: int | None = None,
    survivors: list[str] | None = None,
    called: list[str] | None = None,
    abstained: bool = False,
    prompt: str = "synthetic replay row",
) -> dict[str, Any]:
    shortlist = shortlist if shortlist is not None else [NOTION]
    return {
        "ts": ts,
        "workspace_path": workspace,
        "reducer_version": "imp24.evidence_gated_reducer.v2",
        "reducer_abstained": abstained,
        "reducer_shortlisted_tools": shortlist,
        "reducer_feature_summary": {
            "positive_evidence_tools": (
                n_evidence if n_evidence is not None else len(shortlist)
            )
        },
        "survivor_names_sorted": sorted(
            survivors if survivors is not None else [NOTION, EXA, ORACLE]
        ),
        "surface_state_by_tool": {},
        "prompt_hash": f"ph-{ts}",
        "prompt_excerpt": prompt,
        "tool_call_sequence": [{"tool_name": t} for t in (called or [])],
    }


def _run_replay(
    tmp_path: Path, rows: list[dict[str, Any]], *extra_args: str
) -> tuple[int, str]:
    log = tmp_path / "decisions.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(log), *extra_args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return proc.returncode, proc.stdout


def test_gate_passes_on_clean_corpus(tmp_path: Path) -> None:
    rows = [_row(float(i), workspace=f"/ws/{i}", called=[NOTION]) for i in range(2000)]
    code, out = _run_replay(tmp_path, rows)
    assert "CALLED-tool-would-be-DEMOTED rows: 0" in out
    assert "Gate 2 (shadow→live demote): PASS" in out
    assert code == 0


def test_gate_fails_on_demoted_calls_despite_high_hit_rate(tmp_path: Path) -> None:
    # First call hits the shortlist (100% first-call hit rate) but the second
    # call would be demoted: hit rate cannot mask the miss.
    rows = [
        _row(float(i), workspace=f"/ws/{i}", called=[NOTION, EXA]) for i in range(2000)
    ]
    code, out = _run_replay(tmp_path, rows)
    assert "first-call hit rate:        100.0%" in out
    assert "CALLED-tool-would-be-DEMOTED rows: 2000 (100.00%)" in out
    assert "Gate 2 (shadow→live demote): FAIL" in out
    assert code == 1


def test_unscoreable_rows_are_excluded_and_reported(tmp_path: Path) -> None:
    rows = [
        _row(1.0, abstained=True, called=[NOTION]),
        _row(2.0, abstained=True),
        _row(3.0, shortlist=[], n_evidence=0, called=[NOTION]),
        _row(4.0, called=[]),  # shortlist but no observed call
        _row(5.0, called=[NOTION]),  # the only scoreable row
    ]
    code, out = _run_replay(tmp_path, rows)
    assert "excluded - abstained:       2" in out
    assert "excluded - empty shortlist: 1" in out
    assert "excluded - no tool call:    1" in out
    assert "scoreable (call+shortlist): 1" in out
    # Too few scoreable rows: the gate must not pass on a thin corpus.
    assert code == 1


def test_since_window_keeps_recency_causality_across_boundary(
    tmp_path: Path,
) -> None:
    # Pre-window row calls oracle in /ws; the in-window row re-calls oracle
    # within the TTL, so reconstruction shields it — not a miss.
    rows = [
        _row(1000.0, workspace="/ws", called=[ORACLE]),
        _row(1500.0, workspace="/ws", called=[ORACLE]),
    ]
    code, out = _run_replay(tmp_path, rows, "--since", "1200")
    assert "excluded - before window:   1" in out
    assert "scoreable (call+shortlist): 1" in out
    assert "CALLED-tool-would-be-DEMOTED rows: 0" in out

    # Control: without the shield (different workspace) the same call is a miss.
    rows_cold = [
        _row(1000.0, workspace="/ws", called=[ORACLE]),
        _row(1500.0, workspace="/ws/other", called=[ORACLE]),
    ]
    _, out_cold = _run_replay(tmp_path, rows_cold, "--since", "1200")
    assert "CALLED-tool-would-be-DEMOTED rows: 1" in out_cold


def test_broad_logged_shortlist_is_recapped_to_15(tmp_path: Path) -> None:
    # v1-style row: 30 evidence tools logged; the replayed v2 shortlist is the
    # first 15, so the reported median stays honest at <= 15.
    broad = [f"mcp__srv{i}__tool{i}" for i in range(30)]
    rows = [
        _row(
            float(i),
            workspace=f"/ws/{i}",
            shortlist=broad,
            n_evidence=30,
            survivors=broad + [ORACLE],
            called=[broad[0]],
        )
        for i in range(3)
    ]
    code, out = _run_replay(tmp_path, rows)
    assert "shortlist size med/p75/p90: 15/15/15" in out
    assert "CALLED-tool-would-be-DEMOTED rows: 0" in out
    # Tool 16+ of the logged list is outside the v2 prefix → would be a miss.
    rows_tail = rows + [
        _row(
            100.0,
            workspace="/ws/tail",
            shortlist=broad,
            n_evidence=30,
            survivors=broad + [ORACLE],
            called=[broad[20]],
        )
    ]
    _, out_tail = _run_replay(tmp_path, rows_tail)
    assert "CALLED-tool-would-be-DEMOTED rows: 1" in out_tail
