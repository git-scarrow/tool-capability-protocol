"""CLI runner for TCP harness benchmarks. Outputs JSON summary artifact.

Usage:
    python -m tcp.harness.run_benchmark [--repetitions N] [--output PATH]
    python tcp/harness/run_benchmark.py [--repetitions N] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TCP harness benchmark suite")
    parser.add_argument(
        "--repetitions", type=int, default=5, help="Number of benchmark repetitions"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmark-summary.json",
        help="Output path for JSON summary artifact",
    )
    parser.add_argument(
        "--suite",
        choices=["mt2", "mt3", "all"],
        default="all",
        help="Which benchmark suite to run",
    )
    args = parser.parse_args()

    results: dict[str, object] = {}
    failed = False

    if args.suite in ("mt2", "all"):
        print("Running MT-2 benchmark...", flush=True)
        mt2 = _run_mt2(args.repetitions)
        results["mt2"] = mt2
        if not mt2["pass"]:
            failed = True

    if args.suite in ("mt3", "all"):
        print("Running MT-3 benchmark...", flush=True)
        mt3 = _run_mt3(args.repetitions)
        results["mt3"] = mt3
        if not mt3["pass"]:
            failed = True

    # Write artifact
    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nArtifact written to {output_path}", flush=True)

    # Report
    print("\n=== Benchmark Summary ===", flush=True)
    for suite_name, suite_result in results.items():
        status = "PASS" if suite_result["pass"] else "FAIL"
        print(f"  {suite_name}: {status}", flush=True)
        for check in suite_result.get("checks", []):
            icon = "ok" if check["pass"] else "FAIL"
            print(f"    [{icon}] {check['name']}: {check['value']}", flush=True)

    if failed:
        print("\nBenchmark FAILED", flush=True)
        return 1

    print("\nAll benchmarks PASSED", flush=True)
    return 0


def _run_mt2(repetitions: int) -> dict:
    from tcp.harness.benchmark import (
        benchmark_exposure_suite,
        build_mt2_fixture_set,
    )

    descriptors, tasks, environment = build_mt2_fixture_set()
    suite = benchmark_exposure_suite(descriptors, tasks, environment, repetitions=repetitions)
    s = suite.summary

    checks = [
        {
            "name": "bitmask_false_allows == 0",
            "value": s["bitmask_false_allows"],
            "pass": s["bitmask_false_allows"] == 0,
        },
        {
            "name": "bitmask_false_rejections == 0",
            "value": s["bitmask_false_rejections"],
            "pass": s["bitmask_false_rejections"] == 0,
        },
        {
            "name": "schema_false_allows == 0",
            "value": s["schema_false_allows"],
            "pass": s["schema_false_allows"] == 0,
        },
        {
            "name": "mean_prompt_bytes_reduction > 500",
            "value": s["mean_prompt_bytes_reduction"],
            "pass": s["mean_prompt_bytes_reduction"] > 500,
        },
    ]

    return {
        "suite": "mt2",
        "corpus_size": len(descriptors),
        "task_count": len(tasks),
        "repetitions": repetitions,
        "summary": s,
        "checks": checks,
        "pass": all(c["pass"] for c in checks),
    }


def _run_mt3(repetitions: int) -> dict:
    from tcp.harness.benchmark_mt3 import run_mt3_benchmark

    results = run_mt3_benchmark(repetitions=repetitions)
    s = results["suite_summary"]
    corpus = results["corpus"]

    checks = [
        {
            "name": "corpus_size >= 50",
            "value": corpus["total_descriptors"],
            "pass": corpus["total_descriptors"] >= 50,
        },
        {
            "name": "bitmask_false_allows == 0",
            "value": s["bitmask_false_allows"],
            "pass": s["bitmask_false_allows"] == 0,
        },
        {
            "name": "bitmask_false_rejections == 0",
            "value": s["bitmask_false_rejections"],
            "pass": s["bitmask_false_rejections"] == 0,
        },
        {
            "name": "schema_false_allows == 0",
            "value": s["schema_false_allows"],
            "pass": s["schema_false_allows"] == 0,
        },
        {
            "name": "mean_prompt_bytes_reduction > 500",
            "value": s["mean_prompt_bytes_reduction"],
            "pass": s["mean_prompt_bytes_reduction"] > 500,
        },
        {
            "name": "mean_gating_latency_delta_ms < 5",
            "value": round(s["mean_gating_latency_delta_ms"], 4),
            "pass": s["mean_gating_latency_delta_ms"] < 5,
        },
    ]

    return {
        "suite": "mt3",
        "corpus_size": corpus["total_descriptors"],
        "corpus_sources": len(corpus["sources"]),
        "corpus_categories": len(corpus["categories"]),
        "task_count": results["task_count"],
        "repetitions": results["repetitions"],
        "summary": s,
        "checks": checks,
        "pass": all(c["pass"] for c in checks),
    }


if __name__ == "__main__":
    sys.exit(main())
