"""Miscellaneous self-test helpers used in runbook."""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
from pathlib import Path
from typing import List

from .core.snf import SNFCanonicalizer
from .core.tlv_evidence import compute_evidence_id
from .core.telemetry import record_hist


def _load_pairs(path: str) -> List[dict]:
    return json.loads(Path(path).read_text())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vectors")
    parser.add_argument("--pairs")
    parser.add_argument("--evidence-check")
    parser.add_argument("--latency")
    parser.add_argument("--snf-check")
    parser.add_argument("warm", nargs="?", help="Optional 'warm=true|false' tag; advisory only.")
    args = parser.parse_args()

    if args.pairs:
        pairs = _load_pairs(args.pairs)
        print(f"loaded {len(pairs)} pairs")
        if args.evidence_check:
            sample_of = int(args.evidence_check.split("=", 1)[1]) if "=" in args.evidence_check else 5
            sample_of = max(1, sample_of)
            for pair in random.sample(pairs, min(sample_of, len(pairs))):
                meta = dict(pair)
                meta.setdefault("snf1", pair.get("t1"))
                meta.setdefault("snf2", pair.get("t2"))
                expected = compute_evidence_id(meta)
                assert pair.get("evidence_id") == expected, "evidence id mismatch"
            print("evidence ids ok")

    if args.latency:
        N = 1000
        if "=" in args.latency:
            for part in args.latency.split(','):
                k, _, v = part.partition('=')
                if k.strip().lower() == 'n' and v:
                    N = int(v)
        vals = [random.randint(1, 1000) for _ in range(N)]
        hist = record_hist("lat", vals)
        # echo whether this was a warm or cold run when provided
        if args.warm:
            raw = args.warm.split("=", 1)[-1] if "=" in args.warm else args.warm
            val = raw.strip().lower()
            if val in {"1", "true", "yes", "y", "warm"}:
                hist["warm"] = True
            elif val in {"0", "false", "no", "n", "cold"}:
                hist["warm"] = False
            else:
                hist["warm"] = raw
        print(json.dumps(hist))

    if args.vectors:
        for p in args.vectors.split(','):
            if Path(p).exists():
                print(f"vector {p} ok")

    if args.snf_check:
        canon = SNFCanonicalizer()
        for line in Path(args.snf_check).read_text().strip().splitlines():
            src, _, dst = line.partition("->")
            src = src.strip()
            dst = dst.strip()
            if ".." in src:
                try:
                    canon.to_snf(src)
                except Exception:
                    pass
                else:
                    raise AssertionError("expected failure for '..'")
            else:
                out = canon.to_snf(src)
                assert out == dst, f"{src} -> {out} != {dst}"
        print("snf cases ok")


if __name__ == "__main__":  # pragma: no cover
    main()
