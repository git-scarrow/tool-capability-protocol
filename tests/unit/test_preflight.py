"""Tests for EXP-2 offline pre-flight validation."""

from __future__ import annotations

import re

import pytest

from tcp.agent.preflight import PreflightCheck, PreflightReport, run_preflight


class TestPreflightCheck:
    """PreflightCheck structure."""

    def test_frozen(self):
        c = PreflightCheck(name="test", passed=True, message="ok")
        with pytest.raises(AttributeError):
            c.name = "x"  # type: ignore[misc]

    def test_failed_check(self):
        c = PreflightCheck(name="bad", passed=False, message="broke", details=("a",))
        assert not c.passed
        assert c.details == ("a",)


class TestPreflightReport:
    """PreflightReport aggregation."""

    def test_all_passed(self):
        checks = [
            PreflightCheck(name="a", passed=True, message="ok"),
            PreflightCheck(name="b", passed=True, message="ok"),
        ]
        report = PreflightReport(checks=tuple(checks))
        assert report.passed is True

    def test_any_failed(self):
        checks = [
            PreflightCheck(name="a", passed=True, message="ok"),
            PreflightCheck(name="b", passed=False, message="bad"),
        ]
        report = PreflightReport(checks=tuple(checks))
        assert report.passed is False

    def test_summary_string(self):
        checks = [
            PreflightCheck(name="a", passed=True, message="ok"),
            PreflightCheck(name="b", passed=False, message="bad"),
        ]
        report = PreflightReport(checks=tuple(checks))
        s = report.summary()
        assert "PASS" in s
        assert "FAIL" in s
        assert "a" in s
        assert "b" in s


class TestRunPreflight:
    """Full preflight against real MT-3 corpus."""

    def test_all_checks_pass(self):
        report = run_preflight()
        for check in report.checks:
            assert check.passed, f"Check {check.name!r} failed: {check.message}"
        assert report.passed

    def test_expected_check_names(self):
        report = run_preflight()
        names = {c.name for c in report.checks}
        expected = {
            "tool_name_format",
            "schema_structure",
            "description_nonempty",
            "no_duplicate_names",
            "mock_coverage",
            "expected_tools_in_corpus",
            "filtered_sets_nonempty",
            "filtered_subset_of_corpus",
            "corpus_size_sane",
        }
        assert names == expected

    def test_summary_is_string(self):
        report = run_preflight()
        s = report.summary()
        assert isinstance(s, str)
        assert len(s) > 0
