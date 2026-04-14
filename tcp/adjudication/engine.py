from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Row = dict[str, Any]
_RATER_PREFIXES = {"rater1", "rater2"}
_SYNC_FIELDS = (
    "label_status",
    "rater1_flags",
    "rater1_formats",
    "rater1_notes",
    "rater2_flags",
    "rater2_formats",
    "rater2_notes",
    "adjudication_required",
    "final_flags",
    "final_formats",
    "final_notes",
)


@dataclass(frozen=True)
class CalibrationSummary:
    total_rows: int
    rater1_complete: int
    rater2_complete: int
    paired_rows: int
    agreed_rows: int
    disagreement_rows: int
    final_rows: int
    pending_rows: int
    status_counts: dict[str, int]
    flags_kappa: float | None
    formats_kappa: float | None


def load_rows(path: str | Path) -> list[Row]:
    rows: list[Row] = []
    file_path = Path(path)
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def save_rows(path: str | Path, rows: list[Row]) -> None:
    file_path = Path(path)
    with file_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def apply_rating(
    rows: list[Row],
    *,
    session_id: str,
    turn_id: int,
    rater: str,
    flags: int,
    formats: list[str],
    notes: str = "",
) -> Row:
    if rater not in _RATER_PREFIXES and rater != "final":
        raise ValueError(f"unsupported rater '{rater}'")

    row = _find_row(rows, session_id=session_id, turn_id=turn_id)
    normalized_formats = _normalize_formats(formats)

    row[f"{rater}_flags"] = int(flags)
    row[f"{rater}_formats"] = normalized_formats
    row[f"{rater}_notes"] = notes

    _refresh_row_state(row)
    return row


def sync_rows_by_key(source_rows: list[Row], target_rows: list[Row]) -> int:
    source_by_key = {_row_key(row): row for row in source_rows}
    updated = 0
    for target in target_rows:
        source = source_by_key.get(_row_key(target))
        if source is None:
            continue
        for field in _SYNC_FIELDS:
            target[field] = source.get(field)
        _refresh_row_state(target)
        updated += 1
    return updated


def compute_calibration_summary(rows: list[Row]) -> CalibrationSummary:
    paired_rows = [row for row in rows if _has_rating(row, "rater1") and _has_rating(row, "rater2")]
    status_counts = Counter(str(row.get("label_status", "unlabeled")) for row in rows)

    flags_rater1 = [str(row["rater1_flags"]) for row in paired_rows]
    flags_rater2 = [str(row["rater2_flags"]) for row in paired_rows]
    formats_rater1 = [_formats_label(row["rater1_formats"]) for row in paired_rows]
    formats_rater2 = [_formats_label(row["rater2_formats"]) for row in paired_rows]

    agreed_rows = sum(1 for row in paired_rows if _labels_match(row))
    disagreement_rows = len(paired_rows) - agreed_rows
    final_rows = sum(1 for row in rows if _has_rating(row, "final"))
    rater1_complete = sum(1 for row in rows if _has_rating(row, "rater1"))
    rater2_complete = sum(1 for row in rows if _has_rating(row, "rater2"))
    pending_rows = len(rows) - final_rows - agreed_rows

    return CalibrationSummary(
        total_rows=len(rows),
        rater1_complete=rater1_complete,
        rater2_complete=rater2_complete,
        paired_rows=len(paired_rows),
        agreed_rows=agreed_rows,
        disagreement_rows=disagreement_rows,
        final_rows=final_rows,
        pending_rows=pending_rows,
        status_counts=dict(sorted(status_counts.items())),
        flags_kappa=_cohens_kappa(flags_rater1, flags_rater2),
        formats_kappa=_cohens_kappa(formats_rater1, formats_rater2),
    )


def _find_row(rows: list[Row], *, session_id: str, turn_id: int) -> Row:
    for row in rows:
        if _row_key(row) == (session_id, int(turn_id)):
            return row
    raise KeyError(f"no row found for session_id={session_id} turn_id={turn_id}")


def _row_key(row: Row) -> tuple[str, int]:
    return str(row["session_id"]), int(row["turn_id"])


def _normalize_formats(formats: list[str] | None) -> list[str] | None:
    if formats is None:
        return None
    return sorted({str(item) for item in formats})


def _has_rating(row: Row, prefix: str) -> bool:
    flags = row.get(f"{prefix}_flags")
    formats = row.get(f"{prefix}_formats")
    return flags is not None and formats is not None


def _labels_match(row: Row) -> bool:
    if not (_has_rating(row, "rater1") and _has_rating(row, "rater2")):
        return False
    return (
        int(row["rater1_flags"]) == int(row["rater2_flags"])
        and _normalize_formats(row["rater1_formats"]) == _normalize_formats(row["rater2_formats"])
    )


def _formats_label(formats: list[str] | None) -> str:
    normalized = _normalize_formats(formats)
    if normalized is None:
        return "__missing__"
    if not normalized:
        return "__empty__"
    return "|".join(normalized)


def _refresh_row_state(row: Row) -> None:
    if _has_rating(row, "final"):
        row["label_status"] = "final"
        row["adjudication_required"] = False
        return

    if _has_rating(row, "rater1") and _has_rating(row, "rater2"):
        if _labels_match(row):
            row["label_status"] = "calibrated"
            row["adjudication_required"] = False
        else:
            row["label_status"] = "needs_adjudication"
            row["adjudication_required"] = True
        return

    if _has_rating(row, "rater1"):
        row["label_status"] = "rater1_complete"
        row["adjudication_required"] = False
        return

    if _has_rating(row, "rater2"):
        row["label_status"] = "rater2_complete"
        row["adjudication_required"] = False
        return

    row["label_status"] = "unlabeled"
    row["adjudication_required"] = False


def _cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    if len(labels_a) != len(labels_b):
        raise ValueError("label lists must be the same length")
    if not labels_a:
        return None

    total = len(labels_a)
    observed = sum(1 for left, right in zip(labels_a, labels_b) if left == right) / total
    categories = sorted(set(labels_a) | set(labels_b))
    counts_a = Counter(labels_a)
    counts_b = Counter(labels_b)
    expected = sum((counts_a[category] / total) * (counts_b[category] / total) for category in categories)

    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)
