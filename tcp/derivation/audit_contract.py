"""TCP-DS-2: Request derivation audit contract.

Automated comparison of prompt-derived vs proxy-derived tool selection requests.
Reports coverage delta, precision/recall, and divergent turns.

Usage:
    python -m tcp.derivation.audit_contract --audit-set path/to/audit_set.json
    python tcp/derivation/audit_contract.py --audit-set path/to/audit_set.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tcp.derivation.request_derivation import (
    SessionStartEvent,
    derive_request,
    normalize_mcp_git_tool_name,
)
from tcp.harness.models import ToolSelectionRequest


@dataclass
class AuditSample:
    prompt: str
    session: SessionStartEvent
    ground_truth_flags: int
    ground_truth_formats: frozenset[str]
    proxy_decision: Mapping[str, Any] | None = None


@dataclass
class AuditMetrics:
    total_turns: int
    coverage_delta: float
    precision: Mapping[str, float]
    recall: Mapping[str, float]
    divergent_turns: list[dict]


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP request derivation audit")
    parser.add_argument("--audit-set", help="Path to hand-labeled audit set JSON")
    parser.add_argument("--decisions", help="Path to proxy decisions.jsonl for side-by-side")
    args = parser.parse_args()

    if not args.audit_set:
        print("Error: --audit-set is required for this implementation slice.")
        return

    run_evaluation(Path(args.audit_set), Path(args.decisions) if args.decisions else None)


def run_evaluation(audit_path: Path, decisions_path: Path | None = None) -> None:
    samples = _load_audit_set(audit_path)
    if decisions_path:
        samples = _merge_decisions(samples, decisions_path)

    metrics = calculate_metrics(samples)
    print_report(metrics)


def calculate_metrics(samples: Sequence[AuditSample]) -> AuditMetrics:
    divergent = []
    
    # Capability flag stats
    flag_tp = 0
    flag_fp = 0
    flag_fn = 0
    
    # Format stats
    format_tp = 0
    format_fp = 0
    format_fn = 0

    for sample in samples:
        derived = derive_request(sample.prompt, sample.session)
        
        # Flags
        d_flags = derived.required_capability_flags
        gt_flags = sample.ground_truth_flags
        
        # Format (simplification: union of all formats)
        d_formats = derived.required_output_formats
        gt_formats = sample.ground_truth_formats

        # Track divergences for report
        if d_flags != gt_flags or d_formats != gt_formats:
            divergent.append({
                "prompt": sample.prompt[:100],
                "derived_flags": bin(d_flags),
                "gt_flags": bin(gt_flags),
                "derived_formats": sorted(list(d_formats)),
                "gt_formats": sorted(list(gt_formats)),
            })

        # Calculate TP/FP/FN for flags (bitwise)
        for i in range(16):
            mask = 1 << i
            d_has = bool(d_flags & mask)
            gt_has = bool(gt_flags & mask)
            if d_has and gt_has: flag_tp += 1
            elif d_has and not gt_has: flag_fp += 1
            elif not d_has and gt_has: flag_fn += 1

        # Calculate TP/FP/FN for formats
        all_formats = d_formats | gt_formats
        for f in all_formats:
            d_has = f in d_formats
            gt_has = f in gt_formats
            if d_has and gt_has: format_tp += 1
            elif d_has and not gt_has: format_fp += 1
            elif not d_has and gt_has: format_fn += 1

    precision_flags = flag_tp / (flag_tp + flag_fp) if (flag_tp + flag_fp) > 0 else 1.0
    recall_flags = flag_tp / (flag_tp + flag_fn) if (flag_tp + flag_fn) > 0 else 1.0
    
    precision_formats = format_tp / (format_tp + format_fp) if (format_tp + format_fp) > 0 else 1.0
    recall_formats = format_tp / (format_tp + format_fn) if (format_tp + format_fn) > 0 else 1.0

    return AuditMetrics(
        total_turns=len(samples),
        coverage_delta=0.0, # Placeholder for proxy-derived vs prompt-derived delta
        precision={"flags": precision_flags, "formats": precision_formats},
        recall={"flags": recall_flags, "formats": recall_formats},
        divergent_turns=divergent
    )


def print_report(metrics: AuditMetrics) -> None:
    print("=" * 60)
    print("TCP Request Derivation Audit Report")
    print("=" * 60)
    print(f"Total turns: {metrics.total_turns}")
    print()
    print("Capability Flags:")
    print(f"  Precision: {metrics.precision['flags']:.1%}")
    print(f"  Recall:    {metrics.recall['flags']:.1%}")
    print()
    print("Output Formats:")
    print(f"  Precision: {metrics.precision['formats']:.1%}")
    print(f"  Recall:    {metrics.recall['formats']:.1%}")
    print()
    
    if metrics.divergent_turns:
        print(f"Divergent Turns ({len(metrics.divergent_turns)}):")
        for turn in metrics.divergent_turns:
            print(f"  Prompt: {turn['prompt']}")
            print(f"    Derived Flags: {turn['derived_flags']} | GT: {turn['gt_flags']}")
            print(f"    Derived Formats: {turn['derived_formats']} | GT: {turn['gt_formats']}")


def _load_audit_set(path: Path) -> list[AuditSample]:
    raw = path.read_text()
    if path.suffix == ".jsonl":
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        data = json.loads(raw)
    samples = []
    for item in data:
        if item.get("label_status") != "calibrated":
            continue
        session = SessionStartEvent(
            session_id=item.get("session_id", "audit"),
            permission_mode=item.get("permission_mode", "default"),
            cwd=item.get("cwd", "/home/user"),
        )
        samples.append(AuditSample(
            prompt=item["prompt"],
            session=session,
            ground_truth_flags=item.get("ground_truth_flags", 0),
            ground_truth_formats=frozenset(item.get("ground_truth_formats", ["text"])),
        ))
    return samples


def _merge_decisions(samples: list[AuditSample], path: Path) -> list[AuditSample]:
    # Placeholder for matching logic seen in shadow_analysis.py
    return samples


if __name__ == "__main__":
    main()
