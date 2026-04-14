from __future__ import annotations

from pathlib import Path

from tcp.adjudication import (
    apply_rating,
    compute_calibration_summary,
    load_rows,
    save_rows,
    sync_rows_by_key,
)


def _make_row(session_id: str = "session-a", turn_id: int = 1) -> dict[str, object]:
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "prompt": "inspect the codebase and summarize findings",
        "cwd": "/tmp/project",
        "permission_mode": "default",
        "observed_tool_names": ["read_file"],
        "observed_equivalence_classes": ["FILE_READ"],
        "ok_tool_count": 1,
        "coverage_audit_suitable": True,
        "proxy_required_capability_flags": 1,
        "proxy_heuristic_capability_flags": 1,
        "proxy_required_output_formats": ["text"],
        "label_status": "unlabeled",
        "rater1_flags": None,
        "rater1_formats": None,
        "rater1_notes": "",
        "rater2_flags": None,
        "rater2_formats": None,
        "rater2_notes": "",
        "adjudication_required": False,
        "final_flags": None,
        "final_formats": None,
        "final_notes": "",
    }


def test_load_and_save_rows_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    rows = [_make_row(), _make_row(session_id="session-b", turn_id=2)]

    save_rows(path, rows)
    reloaded = load_rows(path)

    assert reloaded == rows


def test_apply_rating_marks_first_rater_progress() -> None:
    rows = [_make_row()]

    row = apply_rating(
        rows,
        session_id="session-a",
        turn_id=1,
        rater="rater1",
        flags=1,
        formats=["text"],
        notes="needs local files",
    )

    assert row["rater1_flags"] == 1
    assert row["rater1_formats"] == ["text"]
    assert row["label_status"] == "rater1_complete"
    assert row["adjudication_required"] is False


def test_matching_second_rater_marks_row_calibrated() -> None:
    rows = [_make_row()]
    apply_rating(rows, session_id="session-a", turn_id=1, rater="rater1", flags=4, formats=["text"])

    row = apply_rating(rows, session_id="session-a", turn_id=1, rater="rater2", flags=4, formats=["text"])

    assert row["label_status"] == "calibrated"
    assert row["adjudication_required"] is False


def test_mismatched_second_rater_requires_adjudication() -> None:
    rows = [_make_row()]
    apply_rating(rows, session_id="session-a", turn_id=1, rater="rater1", flags=4, formats=["text"])

    row = apply_rating(
        rows,
        session_id="session-a",
        turn_id=1,
        rater="rater2",
        flags=8196,
        formats=["text"],
        notes="auth and network required",
    )

    assert row["label_status"] == "needs_adjudication"
    assert row["adjudication_required"] is True


def test_sync_rows_by_key_copies_labeling_fields() -> None:
    source = [_make_row(session_id="session-a", turn_id=1)]
    target = [_make_row(session_id="session-a", turn_id=1), _make_row(session_id="session-b", turn_id=2)]
    apply_rating(source, session_id="session-a", turn_id=1, rater="rater1", flags=1, formats=["text"])

    synced = sync_rows_by_key(source, target)

    assert synced == 1
    assert target[0]["rater1_flags"] == 1
    assert target[0]["label_status"] == "rater1_complete"
    assert target[1]["rater1_flags"] is None


def test_compute_calibration_summary_reports_counts_and_kappa() -> None:
    rows = [_make_row(session_id="session-a", turn_id=1), _make_row(session_id="session-b", turn_id=2)]
    apply_rating(rows, session_id="session-a", turn_id=1, rater="rater1", flags=1, formats=["text"])
    apply_rating(rows, session_id="session-a", turn_id=1, rater="rater2", flags=1, formats=["text"])
    apply_rating(rows, session_id="session-b", turn_id=2, rater="rater1", flags=4, formats=["text"])
    apply_rating(rows, session_id="session-b", turn_id=2, rater="rater2", flags=8196, formats=["text"])

    summary = compute_calibration_summary(rows)

    assert summary.total_rows == 2
    assert summary.paired_rows == 2
    assert summary.agreed_rows == 1
    assert summary.disagreement_rows == 1
    assert summary.flags_kappa == 1 / 3
    assert summary.formats_kappa == 1.0
