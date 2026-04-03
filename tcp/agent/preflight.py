"""Offline pre-flight validation for EXP-2 benchmark.

Runs 9 independent checks against the MT-3 corpus, schema bridge,
mock executors, and filtered schema builder.  Zero API cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tcp.agent.benchmark import build_filtered_schemas
from tcp.agent.mock_executors import MOCK_RESPONSES
from tcp.agent.tasks import build_agent_tasks
from tcp.harness.corpus import build_mcp_corpus
from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class PreflightCheck:
    """Result of a single pre-flight check."""

    name: str
    passed: bool
    message: str
    details: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PreflightReport:
    """Aggregated pre-flight validation results."""

    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> str:
        lines = []
        for c in self.checks:
            tag = "PASS" if c.passed else "FAIL"
            lines.append(f"  [{tag}] {c.name}: {c.message}")
            for d in c.details:
                lines.append(f"         {d}")
        status = "ALL PASSED" if self.passed else "FAILED"
        lines.insert(0, f"Pre-flight: {status}")
        return "\n".join(lines)


def run_preflight() -> PreflightReport:
    """Run all offline pre-flight checks and return a report."""
    entries = build_mcp_corpus()
    schemas = corpus_to_anthropic_schemas(entries)
    tasks = build_agent_tasks()
    corpus_names = {e.descriptor.name for e in entries}

    checks: list[PreflightCheck] = [
        _check_tool_name_format(schemas),
        _check_schema_structure(schemas),
        _check_description_nonempty(schemas),
        _check_no_duplicate_names(schemas),
        _check_mock_coverage(corpus_names),
        _check_expected_tools_in_corpus(tasks, corpus_names),
        _check_filtered_sets_nonempty(tasks, schemas),
        _check_filtered_subset_of_corpus(tasks, schemas),
        _check_corpus_size_sane(entries),
    ]
    return PreflightReport(checks=tuple(checks))


def _check_tool_name_format(schemas: list[dict]) -> PreflightCheck:
    bad = [s["name"] for s in schemas if not _TOOL_NAME_RE.match(s["name"])]
    if bad:
        return PreflightCheck(
            name="tool_name_format",
            passed=False,
            message=f"{len(bad)} tool names fail Anthropic regex",
            details=tuple(bad[:10]),
        )
    return PreflightCheck(
        name="tool_name_format",
        passed=True,
        message=f"All {len(schemas)} tool names match ^[a-zA-Z0-9_-]{{1,64}}$",
    )


def _check_schema_structure(schemas: list[dict]) -> PreflightCheck:
    bad = []
    for s in schemas:
        issues = []
        if not s.get("name"):
            issues.append("missing name")
        if not s.get("description"):
            issues.append("missing description")
        isc = s.get("input_schema")
        if not isinstance(isc, dict):
            issues.append("input_schema not a dict")
        elif isc.get("type") != "object":
            issues.append(f"input_schema.type={isc.get('type')!r}, expected 'object'")
        elif "properties" not in isc:
            issues.append("input_schema missing properties")
        if issues:
            bad.append(f"{s.get('name', '???')}: {', '.join(issues)}")
    if bad:
        return PreflightCheck(
            name="schema_structure",
            passed=False,
            message=f"{len(bad)} schemas have structural issues",
            details=tuple(bad[:10]),
        )
    return PreflightCheck(
        name="schema_structure",
        passed=True,
        message=f"All {len(schemas)} schemas have valid structure",
    )


def _check_description_nonempty(schemas: list[dict]) -> PreflightCheck:
    empty = [s["name"] for s in schemas if not s.get("description", "").strip()]
    if empty:
        return PreflightCheck(
            name="description_nonempty",
            passed=False,
            message=f"{len(empty)} tools have empty descriptions",
            details=tuple(empty[:10]),
        )
    return PreflightCheck(
        name="description_nonempty",
        passed=True,
        message=f"All {len(schemas)} tools have non-empty descriptions",
    )


def _check_no_duplicate_names(schemas: list[dict]) -> PreflightCheck:
    seen: dict[str, int] = {}
    for s in schemas:
        name = s["name"]
        seen[name] = seen.get(name, 0) + 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    if dupes:
        details = tuple(f"{k} (x{v})" for k, v in dupes.items())
        return PreflightCheck(
            name="no_duplicate_names",
            passed=False,
            message=f"{len(dupes)} duplicate tool names",
            details=details,
        )
    return PreflightCheck(
        name="no_duplicate_names",
        passed=True,
        message=f"All {len(schemas)} tool names are unique",
    )


def _check_mock_coverage(corpus_names: set[str]) -> PreflightCheck:
    mock_names = set(MOCK_RESPONSES.keys())
    missing = corpus_names - mock_names
    if missing:
        return PreflightCheck(
            name="mock_coverage",
            passed=False,
            message=f"{len(missing)} corpus tools have no mock response",
            details=tuple(sorted(missing)[:10]),
        )
    return PreflightCheck(
        name="mock_coverage",
        passed=True,
        message=f"All {len(corpus_names)} corpus tools have mock responses",
    )


def _check_expected_tools_in_corpus(tasks: list, corpus_names: set[str]) -> PreflightCheck:
    missing = []
    for t in tasks:
        if t.expected_tool is not None and t.expected_tool not in corpus_names:
            missing.append(f"{t.name}: expects {t.expected_tool}")
    if missing:
        return PreflightCheck(
            name="expected_tools_in_corpus",
            passed=False,
            message=f"{len(missing)} tasks expect tools not in corpus",
            details=tuple(missing),
        )
    return PreflightCheck(
        name="expected_tools_in_corpus",
        passed=True,
        message=f"All task expected_tool values found in corpus",
    )


def _check_filtered_sets_nonempty(tasks: list, schemas: list[dict]) -> PreflightCheck:
    filtered = build_filtered_schemas(tasks, schemas)
    empty = [name for name, s in filtered.items() if not s]
    if empty:
        return PreflightCheck(
            name="filtered_sets_nonempty",
            passed=False,
            message=f"{len(empty)} tasks have zero filtered tools",
            details=tuple(empty),
        )
    counts = [f"{name}: {len(s)}" for name, s in list(filtered.items())[:3]]
    return PreflightCheck(
        name="filtered_sets_nonempty",
        passed=True,
        message=f"All {len(filtered)} tasks have non-empty filtered sets",
        details=tuple(counts),
    )


def _check_filtered_subset_of_corpus(tasks: list, schemas: list[dict]) -> PreflightCheck:
    corpus_ids = {id(s) for s in schemas}
    filtered = build_filtered_schemas(tasks, schemas)
    violations = []
    for name, task_schemas in filtered.items():
        for s in task_schemas:
            if id(s) not in corpus_ids:
                violations.append(f"{name}: {s['name']} is not from corpus")
                break
    if violations:
        return PreflightCheck(
            name="filtered_subset_of_corpus",
            passed=False,
            message=f"{len(violations)} tasks have non-corpus schemas",
            details=tuple(violations[:5]),
        )
    return PreflightCheck(
        name="filtered_subset_of_corpus",
        passed=True,
        message="All filtered schemas are identity-subsets of corpus",
    )


def _check_corpus_size_sane(entries: list) -> PreflightCheck:
    n = len(entries)
    if n < 50 or n > 200:
        return PreflightCheck(
            name="corpus_size_sane",
            passed=False,
            message=f"Corpus size {n} outside sane range [50, 200]",
        )
    return PreflightCheck(
        name="corpus_size_sane",
        passed=True,
        message=f"Corpus size {n} within sane range [50, 200]",
    )
