#!/usr/bin/env python3
"""TCP-MT-16: Compute MT-12 aggregate metrics from decisions.jsonl.

Reads decisions written after the IMP-16 proxy restart and reports:
  - expected_tool_name population rate (kill gate: <5%)
  - ambiguous-lane miss rate
  - mean retry latency penalty (turns with first_tool_correct=False)
  - miss rate by similarity quartile
  - pack-promotion miss rate
  - MT-12 reopen gate assessment

Usage:
    python scripts/mt16_measure.py [--since ISO8601] [--log PATH]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

DECISIONS_LOG = Path.home() / ".tcp-shadow" / "proxy" / "decisions.jsonl"
# IMP-16 proxy restart timestamp
RESTART_TS = "2026-04-15T20:18:00Z"


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_records(log: Path, since: float) -> list[dict]:
    records = []
    with log.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts") or rec.get("timestamp") or rec.get("req_ts")
            if ts is not None and float(ts) >= since:
                records.append(rec)
    return records


def quartile_label(val: float | None, quartiles: list[float]) -> str:
    if val is None:
        return "unknown"
    if val <= quartiles[0]:
        return "Q1"
    if val <= quartiles[1]:
        return "Q2"
    if val <= quartiles[2]:
        return "Q3"
    return "Q4"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=RESTART_TS,
                        help="ISO8601 timestamp — only records after this are analysed")
    parser.add_argument("--log", default=str(DECISIONS_LOG))
    parser.add_argument("--min-hours", type=float, default=4.0,
                        help="Minimum window in hours before full report (kill gate)")
    args = parser.parse_args()

    log = Path(args.log)
    since_ts = parse_ts(args.since)
    now_ts = datetime.now(timezone.utc).timestamp()
    window_hours = (now_ts - since_ts) / 3600

    print(f"TCP-MT-16 Measurement Report")
    print(f"Proxy restart: {args.since}")
    print(f"Window so far: {window_hours:.2f}h  (minimum required: {args.min_hours}h)")
    print()

    if not log.exists():
        print("ERROR: decisions.jsonl not found at", log)
        sys.exit(1)

    records = load_records(log, since_ts)
    total = len(records)
    print(f"Total turns in window: {total}")

    if total == 0:
        print("No records yet. Wait for proxy traffic.")
        sys.exit(0)

    # ── Population rate (kill gate) ────────────────────────────────────────────
    has_expected = [r for r in records if r.get("expected_tool_name") is not None]
    population_rate = len(has_expected) / total * 100
    print(f"\n[KILL GATE] expected_tool_name population rate: "
          f"{len(has_expected)}/{total} = {population_rate:.1f}%")
    if population_rate < 5.0:
        print("  ⛔ KILL CONDITION FIRES — rate < 5%. IMP-16 threshold too tight.")
        print("     Recommendation: survivor_count ≤ k approach insufficient;")
        print("     consider task-match ranking as next approach.")
        if window_hours < args.min_hours:
            print(f"  ⚠ Window only {window_hours:.2f}h — wait for {args.min_hours}h before concluding.")
        sys.exit(2)
    else:
        print(f"  ✅ Population rate above 5% threshold — measurement viable.")

    if window_hours < args.min_hours:
        print(f"\n⚠ Window only {window_hours:.2f}h — {args.min_hours - window_hours:.1f}h remaining.")
        print("  Partial results follow (not final):\n")

    # ── Ambiguous-lane miss rate ────────────────────────────────────────────────
    evaluable = [r for r in has_expected if r.get("first_tool_name") is not None]
    correct = [r for r in evaluable if r.get("first_tool_correct") is True]
    misses = [r for r in evaluable if r.get("first_tool_correct") is False]
    miss_rate = len(misses) / len(evaluable) * 100 if evaluable else float("nan")
    print(f"\nAmbiguous-lane miss rate (evaluable turns): "
          f"{len(misses)}/{len(evaluable)} = {miss_rate:.1f}%")

    # ── Mean retry latency penalty ─────────────────────────────────────────────
    # Proxy records req_ts; latency proxy: turns where first_tool_correct=False
    # incur a retry. We report mean latency of miss turns vs hit turns.
    def get_latency(r: dict) -> float | None:
        lat = r.get("latency_ms") or r.get("response_latency_ms")
        if lat is not None:
            return float(lat)
        # Compute from req_ts if end_ts present
        req = r.get("req_ts") or r.get("ts")
        end = r.get("end_ts")
        if req and end:
            return (float(end) - float(req)) * 1000
        return None

    miss_latencies = [get_latency(r) for r in misses if get_latency(r) is not None]
    hit_latencies = [get_latency(r) for r in correct if get_latency(r) is not None]
    if miss_latencies and hit_latencies:
        mean_miss = statistics.mean(miss_latencies)
        mean_hit = statistics.mean(hit_latencies)
        print(f"\nMean latency — hits: {mean_hit:.0f}ms  misses: {mean_miss:.0f}ms  "
              f"penalty: {mean_miss - mean_hit:+.0f}ms")
    else:
        print("\nMean retry latency penalty: insufficient latency data in records")

    # ── Miss rate by similarity quartile ──────────────────────────────────────
    sim_vals = [r.get("description_similarity_max") for r in evaluable
                if r.get("description_similarity_max") is not None]
    if sim_vals and len(sim_vals) >= 4:
        q1, q2, q3 = (
            statistics.quantiles(sim_vals, n=4)[0],
            statistics.quantiles(sim_vals, n=4)[1],
            statistics.quantiles(sim_vals, n=4)[2],
        )
        print(f"\nMiss rate by similarity quartile (Q1≤{q1:.2f} Q2≤{q2:.2f} Q3≤{q3:.2f}):")
        for qlabel in ("Q1", "Q2", "Q3", "Q4"):
            q_eval = [r for r in evaluable
                      if quartile_label(r.get("description_similarity_max"), [q1, q2, q3]) == qlabel]
            q_miss = [r for r in q_eval if r.get("first_tool_correct") is False]
            rate = len(q_miss) / len(q_eval) * 100 if q_eval else float("nan")
            print(f"  {qlabel}: {len(q_miss)}/{len(q_eval)} = {rate:.1f}%")
    else:
        print("\nMiss rate by similarity quartile: insufficient similarity data")

    # ── Pack-promotion miss rate ───────────────────────────────────────────────
    promoted = [r for r in evaluable if r.get("pack_promoted") or
                r.get("derivation_method") == "pack_promotion"]
    promo_miss = [r for r in promoted if r.get("first_tool_correct") is False]
    promo_miss_rate = len(promo_miss) / len(promoted) * 100 if promoted else float("nan")
    print(f"\nPack-promotion miss rate: {len(promo_miss)}/{len(promoted)} = {promo_miss_rate:.1f}%"
          if promoted else "\nPack-promotion miss rate: no promoted turns in window")

    # ── MT-12 reopen gate ──────────────────────────────────────────────────────
    print("\n── MT-12 reopen gate assessment ──")
    print(f"  Population rate:   {population_rate:.1f}%  (gate fires if <5%)")
    print(f"  Ambiguous miss:    {miss_rate:.1f}%")
    if miss_rate > 50:
        print("  ⚠ Miss rate >50% — MT-12 hypothesis under stress; consider IMP-9 (conservative mode).")
    elif miss_rate < 20:
        print("  ✅ Miss rate <20% — TCP filtering is helping more than hurting.")
    else:
        print("  → Miss rate in 20-50% range — ambiguous; more data needed.")

    if window_hours >= args.min_hours:
        print(f"\n✅ Window complete ({window_hours:.2f}h ≥ {args.min_hours}h). Results are final.")
    else:
        remaining = args.min_hours - window_hours
        print(f"\n⏳ Re-run in ~{remaining:.1f}h for final results.")


if __name__ == "__main__":
    main()
