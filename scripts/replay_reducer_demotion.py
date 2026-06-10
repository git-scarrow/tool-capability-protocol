#!/usr/bin/env python3
"""TCP-IMP-24 promotion-gate replay over the proxy decision log.

Streams ~/.tcp-shadow/proxy/decisions.jsonl and scores the reducer's
shortlist against observed tool calls, reconstructing the demotion-candidate
set per row when the row predates counterfactual logging
(reducer_demotion_candidates, added with imp24.demote.v1).

The operative promotion metric is the fraction of scoreable rows where a
CALLED tool would have been demoted — NOT the shortlist hit rate, which can
be inflated by large shortlists.  Gate 2 (shadow-demote → live-demote)
requires this miss rate < 1% jointly with median shortlist size <= 15 over
>= 2000 scoreable rows.  See the TCP-IMP-24 promotion-readiness audit.

Usage:
    .venv/bin/python scripts/replay_reducer_demotion.py [path-to-decisions.jsonl]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tcp.proxy.cc_proxy import _SAFETY_FLOOR_TOOLS

DEFAULT_LOG = Path.home() / ".tcp-shadow" / "proxy" / "decisions.jsonl"

# Gate 2 thresholds (audit section G).
GATE_MISS_RATE_MAX = 0.01
GATE_MEDIAN_SHORTLIST_MAX = 15
GATE_MIN_SCOREABLE = 2000


def candidate_set(row: dict) -> set[str]:
    """Demotion candidates for a row: logged when available, else replayed
    from the same inputs demotion_candidates() consumes."""
    logged = row.get("reducer_demotion_candidates")
    if isinstance(logged, list):
        return set(logged)
    shortlist = set(row.get("reducer_shortlisted_tools") or [])
    states = row.get("surface_state_by_tool") or {}
    return {
        t
        for t in (row.get("survivor_names_sorted") or [])
        if t.startswith("mcp__")
        and t not in shortlist
        and t not in _SAFETY_FLOOR_TOOLS
        and states.get(t, "active") == "active"
    }


def main() -> int:
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG
    total = reducer_rows = abstained = scoreable = 0
    first_hit = all_calls_hit = per_call_total = per_call_hit = 0
    demoted_called_rows = 0
    shortlist_sizes: list[int] = []
    candidate_counts: Counter[int] = Counter()
    miss_examples: list[dict] = []
    hit_by_bucket: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def bucket(n: int) -> str:
        if n <= 3:
            return "1-3"
        if n <= 8:
            return "4-8"
        if n <= 20:
            return "9-20"
        return "21+"

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            total += 1
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not row.get("reducer_version"):
                continue
            reducer_rows += 1
            if row.get("reducer_abstained"):
                abstained += 1
                continue
            shortlist = row.get("reducer_shortlisted_tools") or []
            if not isinstance(shortlist, list) or not shortlist:
                continue
            seq = row.get("tool_call_sequence") or []
            called = [
                c.get("tool_name")
                for c in seq
                if isinstance(c, dict) and c.get("tool_name")
            ]
            sl = set(shortlist)
            candidates = candidate_set(row)
            candidate_counts[len(candidates)] += 1
            shortlist_sizes.append(len(shortlist))
            if not called:
                continue
            scoreable += 1
            hits = [t in sl for t in called]
            first_hit += hits[0]
            all_calls_hit += all(hits)
            per_call_total += len(hits)
            per_call_hit += sum(hits)
            b = bucket(len(shortlist))
            hit_by_bucket[b][1] += 1
            hit_by_bucket[b][0] += hits[0]
            demoted_called = [t for t in called if t in candidates]
            if demoted_called:
                demoted_called_rows += 1
                if len(miss_examples) < 20:
                    miss_examples.append(
                        {
                            "ts": row.get("ts"),
                            "prompt": (row.get("prompt_excerpt") or "")[:110],
                            "demoted_called": demoted_called,
                            "shortlist_n": len(shortlist),
                            "candidates_n": len(candidates),
                        }
                    )

    print(f"log: {log_path}")
    print(f"total rows:                 {total}")
    print(f"rows with reducer fields:   {reducer_rows}")
    print(f"abstained:                  {abstained}")
    print(f"scoreable (call+shortlist): {scoreable}")
    if not scoreable:
        print("no scoreable rows — nothing to gate on")
        return 1
    miss_rate = demoted_called_rows / scoreable
    sizes = sorted(shortlist_sizes)
    median_sl = sizes[len(sizes) // 2] if sizes else 0
    print(f"first-call hit rate:        {first_hit / scoreable:.1%}")
    print(f"all-calls-hit rate:         {all_calls_hit / scoreable:.1%}")
    print(f"per-call hit rate:          {per_call_hit / max(per_call_total, 1):.1%}")
    print(
        f"CALLED-tool-would-be-DEMOTED rows: {demoted_called_rows} "
        f"({miss_rate:.1%})  <-- operative gate metric"
    )
    print(f"median shortlist size:      {median_sl}")
    print("first-hit rate by shortlist-size bucket:")
    for b in ("1-3", "4-8", "9-20", "21+"):
        h, n = hit_by_bucket[b]
        if n:
            print(f"  {b:>5}: {h}/{n} = {h / n:.1%}")
    top_candidates = ", ".join(
        f"{k}:{v}" for k, v in sorted(candidate_counts.items())[:20]
    )
    print(f"demotion-candidate count dist (head): {top_candidates}")
    print("\nmiss examples (called tool would have been demoted):")
    for ex in miss_examples:
        print(
            f"  {ex['ts']} sl={ex['shortlist_n']} cand={ex['candidates_n']} "
            f"called={ex['demoted_called']} :: {ex['prompt']!r}"
        )

    gate_pass = (
        scoreable >= GATE_MIN_SCOREABLE
        and miss_rate < GATE_MISS_RATE_MAX
        and median_sl <= GATE_MEDIAN_SHORTLIST_MAX
    )
    print(
        f"\nGate 2 (shadow→live demote): "
        f"{'PASS' if gate_pass else 'FAIL'} "
        f"(need scoreable>={GATE_MIN_SCOREABLE}, "
        f"miss<{GATE_MISS_RATE_MAX:.0%}, median shortlist<={GATE_MEDIAN_SHORTLIST_MAX}; "
        f"got scoreable={scoreable}, miss={miss_rate:.1%}, median={median_sl})"
    )
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
