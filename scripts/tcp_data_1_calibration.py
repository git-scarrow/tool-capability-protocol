#!/usr/bin/env python3
"""Advance TCP-DATA-1 calibration with a gustibus-like adjudication loop."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tcp.adjudication import (
    apply_rating,
    compute_calibration_summary,
    load_rows,
    save_rows,
    sync_rows_by_key,
)
from tcp.core.descriptors import CapabilityFlags


RATER1_CALIBRATION_SEED = (
    {
        "session_id": "e4d1609c-e012-4167-8930-cbb6e54666f1",
        "turn_id": 11,
        "flags": int(CapabilityFlags.SUPPORTS_FILES),
        "formats": ["text"],
        "notes": "Debugging request implies inspecting local UI/code assets even though the prompt is terse.",
    },
    {
        "session_id": "e4d1609c-e012-4167-8930-cbb6e54666f1",
        "turn_id": 44,
        "flags": 0,
        "formats": ["text"],
        "notes": "Recommendation question about workflow placement in Notion; no workspace mutation required.",
    },
    {
        "session_id": "c69a635a-14fa-4980-b583-b0ceab729eb9",
        "turn_id": 6,
        "flags": int(CapabilityFlags.SUPPORTS_FILES),
        "formats": ["text"],
        "notes": "Source-grounded analysis over a local codebase requires file access; response remains prose.",
    },
    {
        "session_id": "3e79856c-af9b-4e33-8114-d1e063e8161b",
        "turn_id": 24,
        "flags": int(CapabilityFlags.SUPPORTS_FILES),
        "formats": ["text"],
        "notes": "Checking whether a new document was picked up implies reading local/project files or state.",
    },
    {
        "session_id": "09ff61a1-6077-40ec-9e89-eb76c17319bd",
        "turn_id": 17,
        "flags": int(CapabilityFlags.SUPPORTS_NETWORK),
        "formats": ["text"],
        "notes": "Prompt explicitly instructs use of chatsearch, which is a network-backed tool in this environment.",
    },
    {
        "session_id": "fresh-a389853b-468d-4e19-81cf-3f71da421ab3",
        "turn_id": 17,
        "flags": 0,
        "formats": ["text"],
        "notes": "Fragment reads as advisory prose, not an explicit request to run a database mutation.",
    },
    {
        "session_id": "efccdf24-45e4-47f8-8076-8999d1b87e43",
        "turn_id": 49,
        "flags": int(CapabilityFlags.SUPPORTS_FILES),
        "formats": ["text"],
        "notes": "Proceed-with-code-work instruction implies local file inspection and edits.",
    },
    {
        "session_id": "c69a635a-14fa-4980-b583-b0ceab729eb9",
        "turn_id": 5,
        "flags": int(CapabilityFlags.SUPPORTS_FILES),
        "formats": ["text"],
        "notes": "Deep source analysis over a local leaked codebase requires file access; JSON is not explicitly requested.",
    },
    {
        "session_id": "3a082cb9-d3fa-4b71-87fe-c4b982bd47f2",
        "turn_id": 6,
        "flags": int(CapabilityFlags.SUPPORTS_NETWORK | CapabilityFlags.AUTH_REQUIRED),
        "formats": ["text"],
        "notes": "Replying to an email thread requires authenticated mailbox/network access.",
    },
    {
        "session_id": "ab9a4371-1b0f-4721-afc4-d2e8abcf0369",
        "turn_id": 5,
        "flags": int(CapabilityFlags.SUPPORTS_NETWORK | CapabilityFlags.AUTH_REQUIRED),
        "formats": ["text"],
        "notes": "Searching prior email requires authenticated mailbox/network access.",
    },
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Advance TCP-DATA-1 calibration state")
    subparsers = parser.add_subparsers(dest="command", required=True)

    advance = subparsers.add_parser(
        "advance-rater1",
        help="Apply the first-rater seed to the calibration slice, sync it, and write a status report.",
    )
    advance.add_argument(
        "--package-dir",
        type=Path,
        default=Path("artifacts/tcp-data-1"),
        help="TCP-DATA-1 package directory",
    )

    report = subparsers.add_parser(
        "report",
        help="Write a calibration status markdown report for an existing JSONL file.",
    )
    report.add_argument("--jsonl", type=Path, required=True, help="Calibration JSONL path")
    report.add_argument("--output", type=Path, required=True, help="Markdown report path")

    tui = subparsers.add_parser(
        "rater2-tui",
        help="Run an interactive TUI for the second rater pass.",
    )
    tui.add_argument(
        "--package-dir",
        type=Path,
        default=Path("artifacts/tcp-data-1"),
        help="TCP-DATA-1 package directory",
    )

    adjudicate = subparsers.add_parser(
        "adjudicate-tui",
        help="Run an interactive TUI to resolve disagreements (needs_adjudication).",
    )
    adjudicate.add_argument(
        "--package-dir",
        type=Path,
        default=Path("artifacts/tcp-data-1"),
        help="TCP-DATA-1 package directory",
    )

    finalize = subparsers.add_parser(
        "finalize-rater1",
        help="Run an interactive TUI to label remaining unlabeled turns in candidate_turns.jsonl.",
    )
    finalize.add_argument(
        "--package-dir",
        type=Path,
        default=Path("artifacts/tcp-data-1"),
        help="TCP-DATA-1 package directory",
    )

    args = parser.parse_args()
    if args.command == "advance-rater1":
        advance_rater1(args.package_dir)
        return
    if args.command == "report":
        rows = load_rows(args.jsonl)
        args.output.write_text(render_report(rows, source_name=args.jsonl.name), encoding="utf-8")
        print(f"Wrote calibration report to {args.output}")
        return
    if args.command == "rater2-tui":
        rater2_tui(args.package_dir)
        return
    if args.command == "adjudicate-tui":
        adjudicate_tui(args.package_dir)
        return
    if args.command == "finalize-rater1":
        finalize_rater1(args.package_dir)
        return
    raise AssertionError(f"unhandled command {args.command}")


def advance_rater1(package_dir: Path) -> None:
    calibration_path = package_dir / "calibration_slice.jsonl"
    candidate_path = package_dir / "candidate_turns.jsonl"
    status_path = package_dir / "calibration_status.md"

    calibration_rows = load_rows(calibration_path)
    for seed in RATER1_CALIBRATION_SEED:
        apply_rating(
            calibration_rows,
            session_id=seed["session_id"],
            turn_id=seed["turn_id"],
            rater="rater1",
            flags=seed["flags"],
            formats=seed["formats"],
            notes=seed["notes"],
        )
    save_rows(calibration_path, calibration_rows)

    candidate_rows = load_rows(candidate_path)
    synced = sync_rows_by_key(calibration_rows, candidate_rows)
    save_rows(candidate_path, candidate_rows)

    status_path.write_text(render_report(calibration_rows, source_name=calibration_path.name), encoding="utf-8")

    print(f"Applied rater1 seed to {len(RATER1_CALIBRATION_SEED)} calibration rows.")
    print(f"Synced {synced} rows back into {candidate_path}.")
    print(f"Wrote calibration report to {status_path}.")


def rater2_tui(package_dir: Path) -> None:
    calibration_path = package_dir / "calibration_slice.jsonl"
    candidate_path = package_dir / "candidate_turns.jsonl"
    status_path = package_dir / "calibration_status.md"

    rows = load_rows(calibration_path)
    pending = [row for row in rows if row.get("rater2_flags") is None]

    if not pending:
        print("All calibration rows already have rater2 labels.")
        return

    print(f"Starting rater2 TUI pass for {len(pending)} pending rows.")
    print("Available Flags:")
    for flag in CapabilityFlags:
        print(f"  {flag.value:5} : {flag.name}")
    print("")

    try:
        for i, row in enumerate(pending, 1):
            print(f"--- Row {i}/{len(pending)} ---")
            print(f"Session: {row['session_id']} Turn: {row['turn_id']}")
            print(f"Prompt: {row['prompt']}")
            print(f"Tools:  {row.get('observed_tool_names', [])}")
            print(f"Rater1 Notes: {row.get('rater1_notes', '')}")
            print("")

            while True:
                try:
                    raw_flags = input("Flags (int sum, e.g. 1 for FILES, 4 for NETWORK): ").strip()
                    if not raw_flags:
                        flags = 0
                    else:
                        flags = int(raw_flags)
                    break
                except ValueError:
                    print("Invalid integer. Please try again.")

            formats_str = input("Formats (comma-separated, default 'text'): ").strip()
            formats = [f.strip() for f in formats_str.split(",") if f.strip()] or ["text"]

            notes = input("Notes: ").strip()

            apply_rating(
                rows,
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                rater="rater2",
                flags=flags,
                formats=formats,
                notes=notes,
            )
            print("Row updated.\n")

    except KeyboardInterrupt:
        print("\nInterrupted. Saving progress...")

    save_rows(calibration_path, rows)

    candidate_rows = load_rows(candidate_path)
    synced = sync_rows_by_key(rows, candidate_rows)
    save_rows(candidate_path, candidate_rows)

    status_path.write_text(render_report(rows, source_name=calibration_path.name), encoding="utf-8")

    print(f"Saved {len(rows)} rows to {calibration_path}.")
    print(f"Synced {synced} rows back into {candidate_path}.")
    print(f"Updated status report at {status_path}.")


def adjudicate_tui(package_dir: Path) -> None:
    calibration_path = package_dir / "calibration_slice.jsonl"
    candidate_path = package_dir / "candidate_turns.jsonl"
    status_path = package_dir / "calibration_status.md"

    rows = load_rows(calibration_path)
    pending = [row for row in rows if row.get("label_status") == "needs_adjudication"]

    if not pending:
        print("No rows currently marked 'needs_adjudication'.")
        return

    print(f"Starting Adjudication TUI for {len(pending)} rows.")
    print("Available Flags:")
    for flag in CapabilityFlags:
        print(f"  {flag.value:5} : {flag.name}")
    print("")

    try:
        for i, row in enumerate(pending, 1):
            print(f"--- Disagreement {i}/{len(pending)} ---")
            print(f"Session: {row['session_id']} Turn: {row['turn_id']}")
            print(f"Prompt: {row['prompt']}")
            print(f"Tools:  {row.get('observed_tool_names', [])}")
            print(f"Rater 1: Flags={row['rater1_flags']}, Formats={row['rater1_formats']}, Notes={row['rater1_notes']}")
            print(f"Rater 2: Flags={row['rater2_flags']}, Formats={row['rater2_formats']}, Notes={row['rater2_notes']}")
            print("")

            while True:
                choice = input("Resolve with [1] Rater 1, [2] Rater 2, or [3] Custom? ").strip()
                if choice == "1":
                    flags = row["rater1_flags"]
                    formats = row["rater1_formats"]
                    notes = f"Adjudicated to Rater 1: {row['rater1_notes']}"
                    break
                elif choice == "2":
                    flags = row["rater2_flags"]
                    formats = row["rater2_formats"]
                    notes = f"Adjudicated to Rater 2: {row['rater2_notes']}"
                    break
                elif choice == "3":
                    while True:
                        try:
                            raw_flags = input("Custom Flags (int sum): ").strip()
                            flags = int(raw_flags) if raw_flags else 0
                            break
                        except ValueError:
                            print("Invalid integer.")
                    formats_str = input("Custom Formats (comma-separated): ").strip()
                    formats = [f.strip() for f in formats_str.split(",") if f.strip()] or ["text"]
                    notes = input("Adjudication Notes: ").strip()
                    break
                else:
                    print("Invalid choice.")

            apply_rating(
                rows,
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                rater="final",
                flags=flags,
                formats=formats,
                notes=notes,
            )
            print("Row finalized.\n")

    except KeyboardInterrupt:
        print("\nInterrupted. Saving progress...")

    save_rows(calibration_path, rows)

    candidate_rows = load_rows(candidate_path)
    synced = sync_rows_by_key(rows, candidate_rows)
    save_rows(candidate_path, candidate_rows)

    status_path.write_text(render_report(rows, source_name=calibration_path.name), encoding="utf-8")

    print(f"Saved {len(rows)} rows to {calibration_path}.")
    print(f"Synced {synced} rows back into {candidate_path}.")
    print(f"Updated status report at {status_path}.")


def finalize_rater1(package_dir: Path) -> None:
    candidate_path = package_dir / "candidate_turns.jsonl"
    rows = load_rows(candidate_path)
    pending = [row for row in rows if row.get("label_status") == "unlabeled"]

    if not pending:
        print("No unlabeled rows remain in candidate_turns.jsonl.")
        return

    print(f"Starting Final Rater1 pass for {len(pending)} unlabeled rows.")
    print("Available Flags:")
    for flag in CapabilityFlags:
        print(f"  {flag.value:5} : {flag.name}")
    print("")

    try:
        for i, row in enumerate(pending, 1):
            print(f"--- Row {i}/{len(pending)} ---")
            print(f"Session: {row['session_id']} Turn: {row['turn_id']}")
            print(f"Prompt: {row['prompt']}")
            print(f"Tools:  {row.get('observed_tool_names', [])}")
            print("")

            while True:
                try:
                    raw_flags = input("Flags (int sum): ").strip()
                    flags = int(raw_flags) if raw_flags else 0
                    break
                except ValueError:
                    print("Invalid integer.")

            formats_str = input("Formats (comma-separated, default 'text'): ").strip()
            formats = [f.strip() for f in formats_str.split(",") if f.strip()] or ["text"]
            notes = input("Notes: ").strip()

            # For single-rater final pass, we apply to rater1 then immediately mark as final
            # since the calibration phase is already complete.
            apply_rating(
                rows,
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                rater="rater1",
                flags=flags,
                formats=formats,
                notes=notes,
            )
            # Find the row again in the full list to update its status to final
            # (apply_rating updates state but might not set 'final' if rater2 is missing)
            for r in rows:
                if r["session_id"] == row["session_id"] and r["turn_id"] == row["turn_id"]:
                    r["label_status"] = "final"
                    r["final_flags"] = flags
                    r["final_formats"] = formats
                    r["final_notes"] = f"Finalized by single-rater pass: {notes}"
                    break

            print("Row finalized.\n")

    except KeyboardInterrupt:
        print("\nInterrupted. Saving progress...")

    save_rows(candidate_path, rows)
    print(f"Saved {len(rows)} rows to {candidate_path}.")


def render_report(rows: list[dict[str, object]], *, source_name: str) -> str:
    summary = compute_calibration_summary(rows)

    lines = [
        "# TCP-DATA-1 Calibration Status",
        "",
        f"Source: `{source_name}`",
        "",
        "## Summary",
        "",
        f"- Total rows: {summary.total_rows}",
        f"- Rater 1 complete: {summary.rater1_complete}",
        f"- Rater 2 complete: {summary.rater2_complete}",
        f"- Paired rows: {summary.paired_rows}",
        f"- Agreed rows: {summary.agreed_rows}",
        f"- Disagreements: {summary.disagreement_rows}",
        f"- Finalized rows: {summary.final_rows}",
        f"- Pending rows: {summary.pending_rows}",
        "",
        "## Agreement",
        "",
        f"- Flags kappa: {_format_kappa(summary.flags_kappa)}",
        f"- Formats kappa: {_format_kappa(summary.formats_kappa)}",
        "",
        "## Status Counts",
        "",
    ]
    lines.extend(f"- `{status}`: {count}" for status, count in summary.status_counts.items())
    lines.extend(
        [
            "",
            "## Next Step",
            "",
            "- Run the second rater over the same calibration slice, then recompute agreement and adjudicate any rows marked `needs_adjudication`.",
        ]
    )
    return "\n".join(lines) + "\n"


def _format_kappa(value: float | None) -> str:
    if value is None:
        return "pending second rater"
    return f"{value:.3f}"


if __name__ == "__main__":
    main()
