#!/usr/bin/env python3
"""Prepare the TCP-DATA-1 labeling package from shadow-session telemetry.

Outputs a turn-level label-ready package:
- candidate_turns.jsonl: 50 scorable turns with blank ground-truth fields
- calibration_slice.jsonl: 10 turns for double-label calibration
- protocol.md: labeling instructions and acceptance gates
- adjudication_log.md: template for disagreement resolution
- provenance_and_limitations.md: source description and caveats
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tcp.derivation.request_derivation import (
    PostToolUseEvent,
    SessionStartEvent,
    classify_unscorable,
    derive_request,
    get_equivalence_class,
)

SHADOW_DIR = Path.home() / ".tcp-shadow"
SESSIONS_DIR = SHADOW_DIR / "sessions"
URL_RE = re.compile(r"https?://\S+")


@dataclass
class LabelTurn:
    session_id: str
    turn_id: int
    prompt: str
    cwd: str
    permission_mode: str
    observed_tool_names: list[str]
    observed_equivalence_classes: list[str]
    ok_tool_count: int
    coverage_audit_suitable: bool
    proxy_required_capability_flags: int
    proxy_heuristic_capability_flags: int
    proxy_required_output_formats: list[str]
    label_status: str
    rater1_flags: int | None
    rater1_formats: list[str] | None
    rater1_notes: str
    rater2_flags: int | None
    rater2_formats: list[str] | None
    rater2_notes: str
    adjudication_required: bool
    final_flags: int | None
    final_formats: list[str] | None
    final_notes: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the TCP-DATA-1 labeling package")
    parser.add_argument("--limit", type=int, default=50, help="Number of labeled turns to prepare")
    parser.add_argument("--calibration-size", type=int, default=10, help="Double-label calibration slice size")
    parser.add_argument("--seed", type=int, default=42, help="Sampling seed")
    parser.add_argument(
        "--package-dir",
        type=Path,
        default=Path("artifacts/tcp-data-1"),
        help="Output directory for the TCP-DATA-1 package",
    )
    args = parser.parse_args()

    turns = collect_turns()
    if len(turns) < args.limit:
        raise SystemExit(f"Only {len(turns)} eligible turns found; need at least {args.limit}.")

    ranked = sorted(turns, key=turn_score, reverse=True)
    selected = pick_diverse_turns(ranked, args.limit, args.seed)
    calibration = selected[: args.calibration_size]

    args.package_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.package_dir / "candidate_turns.jsonl", selected)
    write_jsonl(args.package_dir / "calibration_slice.jsonl", calibration)
    write_text(args.package_dir / "protocol.md", render_protocol(args.limit, args.calibration_size))
    write_text(args.package_dir / "adjudication_log.md", render_adjudication_template(calibration))
    write_text(args.package_dir / "provenance_and_limitations.md", render_provenance(turns, selected))
    write_text(args.package_dir / "README.md", render_readme())

    print(f"Prepared TCP-DATA-1 package in {args.package_dir}")
    print(f"Eligible turns: {len(turns)}")
    print(f"Candidate turns: {len(selected)}")
    print(f"Calibration turns: {len(calibration)}")


def collect_turns() -> list[LabelTurn]:
    turns: list[LabelTurn] = []
    for session_path in sorted(SESSIONS_DIR.glob("*.jsonl")):
        records = [json.loads(line) for line in session_path.read_text().splitlines() if line.strip()]
        session_start = next((r for r in records if r.get("event") == "session_start"), None)
        if not session_start:
            continue

        session = SessionStartEvent(
            session_id=session_start["session_id"],
            permission_mode=session_start.get("permission_mode", "default"),
            cwd=session_start.get("cwd", ""),
        )

        prompts = {
            r["turn_id"]: r["prompt"]
            for r in records
            if r.get("event") == "user_prompt"
        }
        tool_uses_by_turn: dict[int, list[dict]] = {}
        for record in records:
            if record.get("event") != "tool_use":
                continue
            tool_uses_by_turn.setdefault(record["turn_id"], []).append(record)

        for turn_id, prompt in prompts.items():
            tool_uses = tool_uses_by_turn.get(turn_id, [])
            if not tool_uses:
                continue
            if not is_labelable_turn(prompt, tool_uses):
                continue

            derived = derive_request(prompt, session)
            observed_tool_names = sorted({tool["tool_name"] for tool in tool_uses if tool.get("tool_result_status") == "ok"})
            observed_equivalence_classes = sorted(
                {get_equivalence_class(tool["tool_name"], {}) for tool in tool_uses if tool.get("tool_result_status") == "ok"}
            )

            turns.append(
                LabelTurn(
                    session_id=session.session_id,
                    turn_id=turn_id,
                    prompt=prompt,
                    cwd=session.cwd,
                    permission_mode=session.permission_mode,
                    observed_tool_names=observed_tool_names,
                    observed_equivalence_classes=observed_equivalence_classes,
                    ok_tool_count=sum(1 for tool in tool_uses if tool.get("tool_result_status") == "ok"),
                    coverage_audit_suitable=len(observed_equivalence_classes) == 1,
                    proxy_required_capability_flags=derived.required_capability_flags,
                    proxy_heuristic_capability_flags=derived.heuristic_capability_flags,
                    proxy_required_output_formats=sorted(derived.required_output_formats),
                    label_status="unlabeled",
                    rater1_flags=None,
                    rater1_formats=None,
                    rater1_notes="",
                    rater2_flags=None,
                    rater2_formats=None,
                    rater2_notes="",
                    adjudication_required=False,
                    final_flags=None,
                    final_formats=None,
                    final_notes="",
                )
            )
    return turns


def is_labelable_turn(prompt: str, tool_uses: list[dict]) -> bool:
    stripped = prompt.strip()
    tokens = stripped.split()
    has_url = bool(URL_RE.search(stripped))
    if len(tokens) < 5 and not has_url:
        return False

    ok_tool_uses = [tool for tool in tool_uses if tool.get("tool_result_status") == "ok"]
    if not ok_tool_uses:
        return False

    for tool in ok_tool_uses:
        event = PostToolUseEvent(
            session_id=tool["session_id"],
            tool_name=tool["tool_name"],
            tool_input={},
            tool_use_id=tool.get("tool_use_id", ""),
            tool_result_status=tool.get("tool_result_status", "ok"),
        )
        if not classify_unscorable(prompt, event):
            return True
    return False


def turn_score(turn: LabelTurn) -> tuple[int, int, int, int]:
    score = 0
    if turn.coverage_audit_suitable:
        score += 4
    if turn.proxy_required_capability_flags:
        score += 3
    if "json" in turn.proxy_required_output_formats:
        score += 2
    if URL_RE.search(turn.prompt):
        score += 1
    return (
        score,
        len(turn.prompt.split()),
        len(turn.observed_equivalence_classes),
        turn.ok_tool_count,
    )


def pick_diverse_turns(turns: list[LabelTurn], limit: int, seed: int) -> list[LabelTurn]:
    rng = random.Random(seed)
    by_class: dict[str, list[LabelTurn]] = {}
    for turn in turns:
        key = ",".join(turn.observed_equivalence_classes)
        by_class.setdefault(key, []).append(turn)
    for bucket in by_class.values():
        rng.shuffle(bucket)

    selected: list[LabelTurn] = []
    seen: set[tuple[str, int]] = set()
    buckets = sorted(by_class.items(), key=lambda item: len(item[1]), reverse=True)
    while len(selected) < limit and buckets:
        next_buckets: list[tuple[str, list[LabelTurn]]] = []
        for _, bucket in buckets:
            while bucket and (bucket[0].session_id, bucket[0].turn_id) in seen:
                bucket.pop(0)
            if not bucket:
                continue
            turn = bucket.pop(0)
            selected.append(turn)
            seen.add((turn.session_id, turn.turn_id))
            if len(selected) >= limit:
                break
            if bucket:
                next_buckets.append(("", bucket))
        buckets = next_buckets
    return selected[:limit]


def write_jsonl(path: Path, rows: list[LabelTurn]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(asdict(row), sort_keys=True) + "\n")


def write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def render_protocol(limit: int, calibration_size: int) -> str:
    return f"""# TCP-DATA-1 Labeling Protocol

This package is the execution artifact for TCP-DATA-1. It replaces the synthetic audit path for validation purposes.

## Goal

Produce a real hand-labeled audit set that can unblock TCP-VAL-1.

## Label each row with

Fill these concrete fields in the JSONL rows:

- `rater1_flags`, `rater1_formats`, `rater1_notes`
- `rater2_flags`, `rater2_formats`, `rater2_notes` for calibration or second-pass review
- `adjudication_required`
- `final_flags`, `final_formats`, `final_notes`
- `label_status` as `unlabeled`, `rater1_complete`, `rater2_complete`, `calibrated`, `needs_adjudication`, or `final`

## Capability flag semantics

- `SUPPORTS_FILES = 1`
- `SUPPORTS_NETWORK = 4`
- `AUTH_REQUIRED = 8192`

Use bitwise OR when multiple capabilities are required.

## Output format semantics

- Default: `text`
- Add `json` only when the prompt explicitly asks for structured JSON
- Add `binary` only when the prompt explicitly asks for file-like or binary output

## Calibration slice

- Double-label the first {calibration_size} rows in `calibration_slice.jsonl`
- Compare labels and compute Cohen's kappa
- Target: `kappa >= 0.80`
- Maximum attempts before halt: 2
- Suggested workflow: `python3 scripts/tcp_data_1_calibration.py advance-rater1 --package-dir artifacts/tcp-data-1`

## Acceptance gates

- Hand-labeled candidate set contains at least {limit} turns
- At least 20 turns are suitable for coverage-delta audit
- Full set is suitable for precision/recall scoring
- Calibration completed and agreement threshold met
- Adjudication log completed for all disagreements

## Blocking artifact for TCP-VAL-1

The blocker clears when `candidate_turns.jsonl` has final hand labels populated and the calibration/adjudication artifacts are complete.

## Notes

The synthetic audit generator is development-only smoke coverage. Do not use it as validation evidence.
"""


def render_adjudication_template(calibration: list[LabelTurn]) -> str:
    lines = [
        "# TCP-DATA-1 Adjudication Log",
        "",
        "| Row | Session | Turn | Disagreement | Resolution | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for idx, turn in enumerate(calibration, 1):
        lines.append(f"| {idx} | {turn.session_id} | {turn.turn_id} |  |  |  |")
    return "\n".join(lines)


def render_provenance(all_turns: list[LabelTurn], selected: list[LabelTurn]) -> str:
    by_class = Counter(
        cls
        for turn in selected
        for cls in turn.observed_equivalence_classes
    )
    suitable = sum(1 for turn in selected if turn.coverage_audit_suitable)
    return f"""# TCP-DATA-1 Provenance and Limitations

## Source

- Source corpus: `~/.tcp-shadow/sessions/*.jsonl`
- Selection rule: turn-level prompts with enough evidence to score
- Total eligible turns observed in corpus: {len(all_turns)}
- Selected turns in package: {len(selected)}
- Coverage-audit-suitable turns in package: {suitable}
- Unique sessions in selected package: {len({turn.session_id for turn in selected})}
- Unique sessions in full eligible corpus: {len({turn.session_id for turn in all_turns})}

## Observed class mix in selected package

{chr(10).join(f"- {name}: {count}" for name, count in sorted(by_class.items()))}

## Limitations

- This package contains label-ready turns, not final hand labels.
- Proxy-derived fields are included for scoring context, but they are not ground truth.
- Some projects and workflows may still be over-represented.
- The package excludes low-information prompts and turns classified as unscorable by TCP-DS-2 rules.
"""


def render_readme() -> str:
    return """# TCP-DATA-1 Package

Files:

- `candidate_turns.jsonl`: fill in hand labels here
- `calibration_slice.jsonl`: double-label this slice first
- `protocol.md`: labeling instructions and acceptance gates
- `adjudication_log.md`: record disagreements and resolutions
- `provenance_and_limitations.md`: source and caveats
"""


if __name__ == "__main__":
    main()
