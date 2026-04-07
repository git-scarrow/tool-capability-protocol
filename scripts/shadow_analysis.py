#!/usr/bin/env python3
"""TCP shadow pilot — batch analysis script.

Reads a session JSONL log + inventory snapshot, runs TCP's derivation contract
and gate_tools() retroactively, and reports per-call coverage + token delta.

Usage:
    python scripts/shadow_analysis.py --session ~/.tcp-shadow/sessions/<id>.jsonl
    python scripts/shadow_analysis.py --all          # all sessions in ~/.tcp-shadow/sessions/
    python scripts/shadow_analysis.py --audit        # run 20-turn kill-condition audit
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

SHADOW_DIR = Path.home() / ".tcp-shadow"
SESSIONS_DIR = SHADOW_DIR / "sessions"
INVENTORIES_DIR = SHADOW_DIR / "inventories"

# Rough token cost: 1 token ≈ 4 chars for tool descriptions
CHARS_PER_TOKEN = 4
# Minimal description length (chars) per tool — from EXP-3 baseline
MINIMAL_DESC_CHARS = 80
FULL_DESC_CHARS = 320  # ~4x minimal, consistent with EXP-3 62% savings


@dataclass
class CallResult:
    session_id: str
    turn_id: int
    call_id: str
    tool_name: str
    equivalence_class: str
    prompt: str
    unscorable: bool
    unscorable_reason: Optional[str]
    in_survivor_set: Optional[bool]  # None if unscorable
    survivor_count: Optional[int]
    full_inventory_tokens: Optional[int]
    filtered_inventory_tokens: Optional[int]
    token_delta: Optional[int]


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP shadow analysis")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", help="Path to a single session JSONL file")
    group.add_argument("--all", action="store_true", help="Analyse all sessions")
    group.add_argument("--audit", action="store_true", help="Run 20-turn kill condition audit")
    args = parser.parse_args()

    if args.session:
        results = analyse_session(Path(args.session))
        print_report(results)
    elif args.all:
        all_results = []
        for path in sorted(SESSIONS_DIR.glob("*.jsonl")):
            all_results.extend(analyse_session(path))
        print_report(all_results)
    elif args.audit:
        run_audit()


def analyse_session(session_path: Path) -> list[CallResult]:
    from tcp.derivation.request_derivation import (
        SessionStartEvent, PostToolUseEvent,
        derive_request, classify_unscorable, get_equivalence_class,
    )
    from tcp.harness.gating import RuntimeEnvironment, gate_tools
    from tcp.harness.models import ToolRecord

    records = [json.loads(l) for l in session_path.read_text().splitlines() if l.strip()]

    # Extract session metadata
    session_start = next((r for r in records if r["event"] == "session_start"), None)
    if not session_start:
        return []

    session_event = SessionStartEvent(
        session_id=session_start["session_id"],
        permission_mode=session_start.get("permission_mode", "default"),
        cwd=session_start.get("cwd", ""),
    )

    # Load inventory snapshot
    snapshot_id = session_start.get("inventory_snapshot_id", "")
    inventory_path = INVENTORIES_DIR / f"{snapshot_id}.json"
    tool_records: list[ToolRecord] = []
    if inventory_path.exists():
        inv = json.loads(inventory_path.read_text())
        for t in inv.get("tools", []):
            tool_records.append(ToolRecord(
                tool_name=t["name"],
                descriptor_source="shadow",
                descriptor_version="1.0",
                capability_flags=t.get("flags", 0),
                risk_level="safe",
            ))

    full_inventory_size = len(tool_records)

    # Build prompt lookup by turn_id
    prompts: dict[int, str] = {
        r["turn_id"]: r["prompt"]
        for r in records if r["event"] == "user_prompt"
    }

    results = []
    for r in records:
        if r["event"] != "tool_use":
            continue

        turn_id = r["turn_id"]
        prompt = prompts.get(turn_id, "")
        tool_name = r["tool_name"]
        tool_input = {}  # features only logged, reconstruct as empty for class lookup

        tool_event = PostToolUseEvent(
            session_id=r["session_id"],
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=r.get("tool_use_id", ""),
            tool_result_status=r.get("tool_result_status", "ok"),
        )

        equiv_class = get_equivalence_class(tool_name, tool_input)

        # Unscorable check
        unscorable = classify_unscorable(prompt, tool_event)
        unscorable_reason = _unscorable_reason(prompt, tool_event) if unscorable else None

        if unscorable or not tool_records:
            results.append(CallResult(
                session_id=r["session_id"],
                turn_id=turn_id,
                call_id=r.get("call_id", ""),
                tool_name=tool_name,
                equivalence_class=equiv_class,
                prompt=prompt[:120],
                unscorable=unscorable,
                unscorable_reason=unscorable_reason,
                in_survivor_set=None,
                survivor_count=None,
                full_inventory_tokens=None,
                filtered_inventory_tokens=None,
                token_delta=None,
            ))
            continue

        # Derive request and gate
        from tcp.harness.models import ToolSelectionRequest
        from tcp.harness.gating import RuntimeEnvironment, gate_tools

        request = derive_request(prompt, session_event)
        env = RuntimeEnvironment(
            network_enabled=(session_event.permission_mode != "plan"),
            file_access_enabled=True,
            stdin_enabled=True,
            installed_tools=frozenset(t.tool_name for t in tool_records),
        )

        gate_result = gate_tools(tool_records, request, env)
        survivors = {t.tool_name for t in gate_result.approved_tools}
        survivors |= {t.tool_name for t in gate_result.approval_required_tools}

        # Coverage: does survivor set contain a tool in the same equivalence class?
        from tcp.derivation.request_derivation import get_equivalence_class as geq
        in_survivor = any(
            geq(s, {}) == equiv_class or s == tool_name
            for s in survivors
        )

        full_tokens = full_inventory_size * FULL_DESC_CHARS // CHARS_PER_TOKEN
        filtered_tokens = len(survivors) * MINIMAL_DESC_CHARS // CHARS_PER_TOKEN
        token_delta = full_tokens - filtered_tokens

        results.append(CallResult(
            session_id=r["session_id"],
            turn_id=turn_id,
            call_id=r.get("call_id", ""),
            tool_name=tool_name,
            equivalence_class=equiv_class,
            prompt=prompt[:120],
            unscorable=False,
            unscorable_reason=None,
            in_survivor_set=in_survivor,
            survivor_count=len(survivors),
            full_inventory_tokens=full_tokens,
            filtered_inventory_tokens=filtered_tokens,
            token_delta=token_delta,
        ))

    return results


def print_report(results: list[CallResult]) -> None:
    scorable = [r for r in results if not r.unscorable]
    total = len(results)
    n_unscorable = total - len(scorable)

    if not scorable:
        print(f"No scorable turns in {total} total events.")
        return

    covered = sum(1 for r in scorable if r.in_survivor_set)
    coverage = covered / len(scorable)
    mean_delta = sum(r.token_delta for r in scorable if r.token_delta) / len(scorable)
    unscorable_rate = n_unscorable / total if total else 0

    print("=" * 60)
    print("TCP Shadow Analysis Report")
    print("=" * 60)
    print(f"Total tool calls:    {total}")
    print(f"Scorable turns:      {len(scorable)} ({1-unscorable_rate:.0%} of total)")
    print(f"Unscorable turns:    {n_unscorable} ({unscorable_rate:.0%}) [target: <15%]")
    print()
    print(f"Coverage (any-pos):  {coverage:.1%}  ({covered}/{len(scorable)})")
    print(f"Mean token delta:    {mean_delta:.0f} tokens/call")
    if scorable and scorable[0].full_inventory_tokens:
        avg_full = sum(r.full_inventory_tokens for r in scorable if r.full_inventory_tokens) / len(scorable)
        print(f"Est. token savings:  {mean_delta/avg_full:.0%}")
    print()

    if coverage < 0.95:
        print("⚠️  Coverage below 95% threshold")
    if unscorable_rate > 0.15:
        print("⚠️  Unscorable rate exceeds 15% — check derivation contract")

    # Per-class breakdown
    from collections import defaultdict, Counter
    by_class: dict[str, list[CallResult]] = defaultdict(list)
    for r in scorable:
        by_class[r.equivalence_class].append(r)

    print(f"{'Class':<20} {'Calls':>6} {'Covered':>8} {'Coverage':>9}")
    print("-" * 47)
    for cls, items in sorted(by_class.items()):
        n = len(items)
        c = sum(1 for r in items if r.in_survivor_set)
        print(f"{cls:<20} {n:>6} {c:>8} {c/n:>8.0%}")


def run_audit() -> None:
    """20-turn kill condition audit (TCP-DS-2 §6).

    Samples 20 scorable turns and reports coverage. Without ground-truth labels,
    this outputs the turns for manual review.
    """
    import random

    all_results: list[CallResult] = []
    for path in sorted(SESSIONS_DIR.glob("*.jsonl")):
        all_results.extend(analyse_session(path))

    scorable = [r for r in all_results if not r.unscorable]
    if len(scorable) < 20:
        print(f"Only {len(scorable)} scorable turns available; need ≥ 20 for audit.")
        return

    sample = random.sample(scorable, 20)
    print("=== 20-Turn Kill Condition Audit Sample ===")
    print("Review these turns manually against independent ground-truth labels.")
    print(f"{'#':<3} {'Tool':<30} {'Class':<18} {'In Surv':>8}  Prompt")
    print("-" * 100)
    for i, r in enumerate(sample, 1):
        print(f"{i:<3} {r.tool_name:<30} {r.equivalence_class:<18} {str(r.in_survivor_set):>8}  {r.prompt[:40]}")

    auto_coverage = sum(1 for r in sample if r.in_survivor_set) / 20
    print(f"\nAuto-derived coverage on sample: {auto_coverage:.0%}")
    print("Compare against manual labels. Kill if delta > 10pp.")


def _unscorable_reason(prompt: str, tool_event) -> str:
    from tcp.derivation.request_derivation import (
        _SYSTEM_TOOLS, _CONTINUATION_PROMPTS, _derive_capability_flags_from_prompt_only
    )
    if tool_event.tool_name in _SYSTEM_TOOLS:
        return "system_tool"
    stripped = prompt.strip().lower()
    if not stripped or stripped in _CONTINUATION_PROMPTS:
        return "continuation_or_empty"
    if tool_event.tool_result_status != "ok":
        return "failed_tool"
    flags = _derive_capability_flags_from_prompt_only(prompt)
    if bin(flags).count("1") >= 3:
        return "multi_capability"
    return "unknown"


if __name__ == "__main__":
    main()
