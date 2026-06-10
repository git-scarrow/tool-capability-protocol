#!/usr/bin/env python3
"""TCP-IMP-24 promotion-gate replay over the proxy decision log.

Streams ~/.tcp-shadow/proxy/decisions.jsonl and scores the CURRENT demotion
policy (enforcement v2: evidence-only shortlist cap 15 + workspace
server-recency shield) against observed tool calls.

Per row the demotion-candidate set is:
  - the logged ``reducer_demotion_candidates`` when the row was produced by
    enforcement v2 (the shield is already baked in), else
  - reconstructed from the same logged inputs demotion_candidates() consumes:
    survivors that are MCP, ACTIVE-surface, non-floor, not in the evidence
    shortlist prefix, and not on a recency-shielded server.  The shielded
    server set comes from ``reducer_recent_servers`` when logged, otherwise it
    is replayed from (workspace_path, ts, tool_call_sequence) — fields present
    on every historical row — using the same TTL as live enforcement.

The operative promotion metric is the fraction of scoreable rows where a
CALLED tool would have been demoted — NOT the shortlist hit rate, which can
be inflated by large shortlists.  Gate 2 (shadow-demote → live-demote)
requires this miss rate < 1% jointly with median shortlist size <= 15 over
>= 2000 scoreable rows.  See the TCP-IMP-24 promotion-readiness audit.

Unscoreable rows (no reducer fields, reducer abstained, empty shortlist, or
no observed tool call) are excluded from every rate; they are counted and
reported so exclusions are visible rather than silent.

Usage:
    .venv/bin/python scripts/replay_reducer_demotion.py [log] [--since EPOCH]

    --since EPOCH   Score only rows with ts >= EPOCH (recent-window gate).
                    Earlier rows still feed the recency reconstruction so the
                    shield at the window boundary is causally correct.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from tcp.proxy.cc_proxy import _SAFETY_FLOOR_TOOLS, _extract_mcp_server
from tcp.proxy.survivor_reducer import ENFORCEMENT_VERSION

DEFAULT_LOG = Path.home() / ".tcp-shadow" / "proxy" / "decisions.jsonl"

# Gate 2 thresholds (audit section G).
GATE_MISS_RATE_MAX = 0.01
GATE_MEDIAN_SHORTLIST_MAX = 15
GATE_MIN_SCOREABLE = 2000

# Must match cc_proxy.RECENCY_TTL_DEFAULT_SECONDS for faithful reconstruction
# of rows that predate reducer_recent_servers logging.
RECENCY_TTL_SECONDS = 1800.0

# Policy parameters replayed onto pre-v2 rows.
SHORTLIST_CAP = 15


def evidence_shortlist(row: dict) -> list[str]:
    """The v2 shortlist for this row: evidence-ranked prefix, cap 15.

    v1 rows logged shortlist = evidence prefix (cap 20) + safety-floor union,
    with the evidence count in feature_summary.positive_evidence_tools; the
    evidence prefix is recoverable exactly because the floor union appended
    floor tools strictly after the capped evidence prefix.
    """
    shortlist = row.get("reducer_shortlisted_tools") or []
    if not isinstance(shortlist, list):
        return []
    fs = row.get("reducer_feature_summary") or {}
    try:
        n_evidence = int(fs.get("positive_evidence_tools") or 0)
    except (TypeError, ValueError):
        n_evidence = len(shortlist)
    return shortlist[: min(SHORTLIST_CAP, n_evidence, len(shortlist))]


def candidate_set(
    row: dict, shortlist: list[str], recent_servers: set[str]
) -> set[str]:
    """Demotion candidates for a row under the current (v2) policy."""
    if row.get("reducer_enforcement_version") == ENFORCEMENT_VERSION:
        logged = row.get("reducer_demotion_candidates")
        if isinstance(logged, list):
            return set(logged)
    if isinstance(row.get("reducer_recent_servers"), list):
        recent_servers = set(row["reducer_recent_servers"])
    sl = set(shortlist)
    states = row.get("surface_state_by_tool") or {}
    out: set[str] = set()
    for t in row.get("survivor_names_sorted") or []:
        server = _extract_mcp_server(t)
        if server is None:
            continue
        if t in sl or t in _SAFETY_FLOOR_TOOLS:
            continue
        if server in recent_servers:
            continue
        if states.get(t, "active") != "active":
            continue
        out.add(t)
    return out


def pctile(sorted_xs: list[int], q: float) -> int:
    if not sorted_xs:
        return 0
    return sorted_xs[min(len(sorted_xs) - 1, int(q * len(sorted_xs)))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", nargs="?", default=str(DEFAULT_LOG))
    ap.add_argument(
        "--since",
        type=float,
        default=0.0,
        help="score only rows with ts >= this epoch (recent-window gate)",
    )
    args = ap.parse_args()
    log_path = Path(args.log)

    total = reducer_rows = abstained = empty_shortlist = 0
    pre_window = no_call = scoreable = 0
    first_hit = all_calls_hit = per_call_total = per_call_hit = 0
    demoted_called_rows = 0
    shortlist_sizes: list[int] = []
    candidate_sizes: list[int] = []
    prompt_hashes: set[str] = set()
    miss_prompt_hashes: set[str] = set()
    miss_tools: Counter[str] = Counter()
    miss_examples: list[dict] = []
    hit_by_bucket: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    # Recency reconstruction registry: workspace -> server -> last call ts.
    recent: dict[str, dict[str, float]] = defaultdict(dict)

    def bucket(n: int) -> str:
        if n <= 3:
            return "1-3"
        if n <= 8:
            return "4-8"
        return "9-15" if n <= 15 else "16+"

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
            ts = row.get("ts") or 0.0
            ws = row.get("workspace_path") or ""
            seq = row.get("tool_call_sequence") or []
            called = [
                c.get("tool_name")
                for c in seq
                if isinstance(c, dict) and isinstance(c.get("tool_name"), str)
            ]

            def note_calls() -> None:
                # Registry update strictly AFTER scoring this row (causality:
                # live enforcement sees only calls from completed turns).
                for t in called:
                    server = _extract_mcp_server(t)
                    if server and ws:
                        prev = recent[ws].get(server)
                        if prev is None or ts > prev:
                            recent[ws][server] = ts

            skip = False
            if row.get("reducer_abstained"):
                abstained += 1
                skip = True
            else:
                shortlist = evidence_shortlist(row)
                if not shortlist:
                    empty_shortlist += 1
                    skip = True
                elif ts < args.since:
                    pre_window += 1
                    skip = True
            if skip:
                note_calls()
                continue

            recent_servers = (
                {
                    s
                    for s, last in recent[ws].items()
                    if ts - last <= RECENCY_TTL_SECONDS
                }
                if ws
                else set()
            )
            candidates = candidate_set(row, shortlist, recent_servers)
            shortlist_sizes.append(len(shortlist))
            candidate_sizes.append(len(candidates))
            if not called:
                no_call += 1
                note_calls()
                continue
            scoreable += 1
            ph = row.get("prompt_hash") or ""
            prompt_hashes.add(ph)
            sl = set(shortlist)
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
                miss_prompt_hashes.add(ph)
                for t in demoted_called:
                    miss_tools[t] += 1
                if len(miss_examples) < 20:
                    miss_examples.append(
                        {
                            "ts": ts,
                            "prompt": (row.get("prompt_excerpt") or "")[:110],
                            "demoted_called": demoted_called,
                            "shortlist_n": len(shortlist),
                            "candidates_n": len(candidates),
                        }
                    )
            note_calls()

    print(f"log: {log_path}")
    if args.since:
        print(f"window: ts >= {args.since}")
    print(f"total rows:                 {total}")
    print(f"rows with reducer fields:   {reducer_rows}")
    print(f"excluded - abstained:       {abstained}")
    print(f"excluded - empty shortlist: {empty_shortlist}")
    print(f"excluded - before window:   {pre_window}")
    print(f"excluded - no tool call:    {no_call}")
    print(f"scoreable (call+shortlist): {scoreable}")
    if not scoreable:
        print("no scoreable rows — nothing to gate on")
        return 1
    miss_rate = demoted_called_rows / scoreable
    uniq_miss_rate = (
        len(miss_prompt_hashes) / len(prompt_hashes) if prompt_hashes else 0.0
    )
    sizes = sorted(shortlist_sizes)
    csizes = sorted(candidate_sizes)
    median_sl = pctile(sizes, 0.5)
    print(f"first-call hit rate:        {first_hit / scoreable:.1%}")
    print(f"all-calls-hit rate:         {all_calls_hit / scoreable:.1%}")
    print(f"per-call hit rate:          {per_call_hit / max(per_call_total, 1):.1%}")
    print(
        f"CALLED-tool-would-be-DEMOTED rows: {demoted_called_rows} "
        f"({miss_rate:.2%})  <-- operative gate metric"
    )
    print(f"unique-prompt miss rate:    {uniq_miss_rate:.2%}")
    print(
        f"shortlist size med/p75/p90: {median_sl}/{pctile(sizes, 0.75)}"
        f"/{pctile(sizes, 0.9)}"
    )
    print(
        f"demotion-candidate count med/p75/p90: {pctile(csizes, 0.5)}"
        f"/{pctile(csizes, 0.75)}/{pctile(csizes, 0.9)}"
    )
    print("first-hit rate by shortlist-size bucket:")
    for b in ("1-3", "4-8", "9-15", "16+"):
        h, n = hit_by_bucket[b]
        if n:
            print(f"  {b:>5}: {h}/{n} = {h / n:.1%}")
    print(f"top miss tools: {miss_tools.most_common(8)}")
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
        f"got scoreable={scoreable}, miss={miss_rate:.2%}, median={median_sl})"
    )
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
