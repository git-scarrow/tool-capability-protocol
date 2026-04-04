# TCP-Gated Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build instrumented experiment apparatus that measures whether TCP pre-filtering reduces Claude's first-token latency, total response time, and input token count without degrading tool-selection correctness.

**Architecture:** Three new components: a schema bridge converting ToolRecords/CorpusEntries to Anthropic API tool schemas, an instrumented async agent loop using the `anthropic` SDK with mock tool executors, and a paired benchmark runner that compares filtered vs unfiltered arms with randomized trial ordering. All code is measurement apparatus, not product.

**Tech Stack:** Python 3.9+, `anthropic` SDK (new dev dependency), `pytest-asyncio`, existing `tcp.harness` modules (`corpus`, `bitmask_filter`, `gating`, `models`, `normalize`)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `tcp/harness/schema_bridge.py` | Convert ToolRecord/CorpusEntry → Anthropic `tools` parameter format |
| `tcp/agent/__init__.py` | Package init with public exports |
| `tcp/agent/tasks.py` | 12 agent experiment task definitions with natural language prompts |
| `tcp/agent/mock_executors.py` | Canned tool responses keyed by tool name |
| `tcp/agent/loop.py` | Instrumented async agent loop with timing + token counting |
| `tcp/agent/benchmark.py` | Paired trial runner, randomized ordering, report generation |
| `tests/unit/test_schema_bridge.py` | Schema bridge unit tests |
| `tests/unit/test_mock_executors.py` | Mock executor tests |
| `tests/unit/test_agent_loop.py` | Agent loop tests (mocked API) |
| `tests/unit/test_agent_benchmark.py` | Benchmark runner tests (mocked loop) |

---

### Task 1: Add anthropic SDK dependency

**Files:**
- Modify: `pyproject.toml:44-57` (dev dependencies group)

- [ ] **Step 1: Add anthropic to dev dependencies**

In `pyproject.toml`, add `anthropic` to the `[tool.poetry.group.dev.dependencies]` section:

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"
pytest-cov = "^4.1.0"
pytest-asyncio = "^0.21.0"
black = "^23.0.0"
isort = "^5.12.0"
flake8 = "^6.0.0"
mypy = "^1.5.0"
pre-commit = "^3.4.0"
sphinx = "^7.1.0"
sphinx-rtd-theme = "^1.3.0"
mkdocs = "^1.5.0"
mkdocs-material = "^9.2.0"
bandit = "^1.8.6"
anthropic = ">=0.39.0"
```

- [ ] **Step 2: Install**

Run: `poetry install --with dev`
Expected: anthropic SDK installed successfully.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "feat(agent): add anthropic SDK dev dependency for EXP-2"
```

---

### Task 2: Schema Bridge

**Files:**
- Create: `tcp/harness/schema_bridge.py`
- Test: `tests/unit/test_schema_bridge.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_schema_bridge.py
"""Tests for the Anthropic schema bridge."""

from __future__ import annotations

import pytest

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CapabilityFlags,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    ParameterDescriptor,
    ParameterType,
    PerformanceMetrics,
    ProcessingMode,
)
from tcp.harness.corpus import CorpusEntry
from tcp.harness.models import ToolRecord
from tcp.harness.schema_bridge import (
    corpus_to_anthropic_schemas,
    tool_record_to_anthropic_schema,
)


def _make_descriptor(
    name: str,
    *,
    commands: list[str] | None = None,
    flags: int = 0,
    description: str = "",
    parameters: list[ParameterDescriptor] | None = None,
) -> CapabilityDescriptor:
    """Test helper to build a minimal descriptor."""
    cmds = []
    for c in (commands or [name]):
        cmd = CommandDescriptor(name=c, parameters=parameters or [])
        cmds.append(cmd)
    return CapabilityDescriptor(
        name=name,
        version="1.0",
        description=description,
        commands=cmds,
        input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        output_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        processing_modes=[ProcessingMode.SYNC],
        capability_flags=flags,
        performance=PerformanceMetrics(avg_processing_time_ms=100, memory_usage_mb=16),
    )


def _make_entry(
    descriptor: CapabilityDescriptor,
    source: str = "test",
    category: str = "test",
) -> CorpusEntry:
    return CorpusEntry(descriptor=descriptor, source=source, category=category)


class TestToolRecordToAnthropicSchema:
    """Tests for tool_record_to_anthropic_schema."""

    def test_basic_structure(self):
        record = ToolRecord(
            tool_name="fs-read-file",
            descriptor_source="test",
            descriptor_version="1.0",
            capability_flags=0,
            risk_level="safe",
            commands=frozenset({"read_file"}),
            input_formats=frozenset({"text"}),
            output_formats=frozenset({"text"}),
        )
        schema = tool_record_to_anthropic_schema(record)

        assert schema["name"] == "fs-read-file"
        assert isinstance(schema["description"], str)
        assert len(schema["description"]) > 0
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"
        assert "properties" in schema["input_schema"]

    def test_auth_required_annotation(self):
        record = ToolRecord(
            tool_name="chmod",
            descriptor_source="test",
            descriptor_version="1.0",
            capability_flags=int(CapabilityFlags.AUTH_REQUIRED),
            risk_level="approval_required",
            commands=frozenset({"chmod"}),
        )
        schema = tool_record_to_anthropic_schema(record)
        assert "[APPROVAL REQUIRED]" in schema["description"]

    def test_no_auth_annotation_when_not_required(self):
        record = ToolRecord(
            tool_name="jq",
            descriptor_source="test",
            descriptor_version="1.0",
            capability_flags=0,
            risk_level="safe",
            commands=frozenset({"jq"}),
        )
        schema = tool_record_to_anthropic_schema(record)
        assert "[APPROVAL REQUIRED]" not in schema["description"]


class TestCorpusToAnthropicSchemas:
    """Tests for corpus_to_anthropic_schemas."""

    def test_returns_list_of_dicts(self):
        entries = [
            _make_entry(_make_descriptor("tool-a", description="Tool A")),
            _make_entry(_make_descriptor("tool-b", description="Tool B")),
        ]
        schemas = corpus_to_anthropic_schemas(entries)
        assert len(schemas) == 2
        assert all(isinstance(s, dict) for s in schemas)

    def test_schema_structure(self):
        entries = [
            _make_entry(_make_descriptor("tool-a", description="Does A")),
        ]
        schema = corpus_to_anthropic_schemas(entries)[0]
        assert schema["name"] == "tool-a"
        assert "Does A" in schema["description"]
        assert schema["input_schema"]["type"] == "object"

    def test_auth_required_annotation(self):
        entries = [
            _make_entry(
                _make_descriptor(
                    "secure-tool",
                    flags=int(CapabilityFlags.AUTH_REQUIRED),
                    description="Secure operation",
                )
            ),
        ]
        schema = corpus_to_anthropic_schemas(entries)[0]
        assert "[APPROVAL REQUIRED]" in schema["description"]

    def test_parameters_mapped_to_input_schema(self):
        params = [
            ParameterDescriptor(
                name="path",
                type=ParameterType.STRING,
                required=True,
                description="File path",
            ),
            ParameterDescriptor(
                name="encoding",
                type=ParameterType.STRING,
                required=False,
                description="File encoding",
            ),
        ]
        entries = [
            _make_entry(
                _make_descriptor("reader", description="Read", parameters=params)
            ),
        ]
        schema = corpus_to_anthropic_schemas(entries)[0]
        props = schema["input_schema"]["properties"]
        assert "path" in props
        assert props["path"]["type"] == "string"
        assert "encoding" in props
        required = schema["input_schema"].get("required", [])
        assert "path" in required
        assert "encoding" not in required

    def test_schema_parity_invariant(self):
        """Both arms must use schemas from the same generation."""
        entries = [
            _make_entry(_make_descriptor("tool-a", description="A")),
            _make_entry(_make_descriptor("tool-b", description="B")),
            _make_entry(_make_descriptor("tool-c", description="C")),
        ]
        all_schemas = corpus_to_anthropic_schemas(entries)
        # Simulate filtered arm: subset by name
        filtered_names = {"tool-a", "tool-c"}
        filtered = [s for s in all_schemas if s["name"] in filtered_names]
        # Verify schemas are identical objects from the same list
        assert filtered[0] is all_schemas[0]
        assert filtered[1] is all_schemas[2]

    def test_mt3_corpus_coverage(self):
        """At least 80% of MT-3 corpus (72+ of 90) should produce valid schemas."""
        from tcp.harness.corpus import build_mcp_corpus

        entries = build_mcp_corpus()
        schemas = corpus_to_anthropic_schemas(entries)
        valid = [
            s
            for s in schemas
            if s.get("name")
            and s.get("description")
            and isinstance(s.get("input_schema"), dict)
            and s["input_schema"].get("type") == "object"
        ]
        coverage = len(valid) / len(entries) if entries else 0
        assert coverage >= 0.80, (
            f"Schema bridge coverage {coverage:.1%} below 80% threshold "
            f"({len(valid)}/{len(entries)})"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_schema_bridge.py -v`
Expected: ImportError — `tcp.harness.schema_bridge` does not exist.

- [ ] **Step 3: Implement schema bridge**

```python
# tcp/harness/schema_bridge.py
"""Convert TCP descriptors to Anthropic API tool schema format.

Schema parity invariant: both arms of a paired benchmark use schemas
generated by the same function.  The filtered arm selects a subset of
the pre-generated list — no re-generation, no reformatting.
"""

from __future__ import annotations

from typing import Sequence

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CapabilityFlags,
    ParameterType,
)
from tcp.harness.corpus import CorpusEntry
from tcp.harness.models import ToolRecord


_PARAM_TYPE_MAP = {
    ParameterType.STRING: "string",
    ParameterType.INTEGER: "integer",
    ParameterType.FLOAT: "number",
    ParameterType.BOOLEAN: "boolean",
    ParameterType.ENUM: "string",
    ParameterType.ARRAY: "array",
    ParameterType.OBJECT: "object",
    ParameterType.FILE: "string",
    ParameterType.BINARY: "string",
}


def _infer_input_schema(descriptor: CapabilityDescriptor) -> dict:
    """Build JSON Schema input_schema from descriptor commands."""
    commands = descriptor.commands
    if isinstance(commands, dict):
        commands = list(commands.values())
    if not commands:
        return {"type": "object", "properties": {}}

    cmd = commands[0]
    if cmd.parameters:
        properties: dict = {}
        required: list[str] = []
        for p in cmd.parameters:
            prop: dict = {"type": _PARAM_TYPE_MAP.get(p.type, "string")}
            if p.description:
                prop["description"] = p.description
            if p.enum_values:
                prop["enum"] = p.enum_values
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        schema: dict = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    # No explicit parameters — provide a minimal input property
    return {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": f"Input for {cmd.name}",
            },
        },
    }


def tool_record_to_anthropic_schema(record: ToolRecord) -> dict:
    """Convert a single ToolRecord into an Anthropic tool schema dict.

    Returns: {"name": ..., "description": ..., "input_schema": {...}}
    """
    parts: list[str] = []
    if record.commands:
        parts.append(f"Commands: {', '.join(sorted(record.commands))}")
    if record.input_formats:
        parts.append(f"Input: {', '.join(sorted(record.input_formats))}")
    if record.output_formats:
        parts.append(f"Output: {', '.join(sorted(record.output_formats))}")

    description = "; ".join(parts) if parts else record.tool_name

    if record.capability_flags & CapabilityFlags.AUTH_REQUIRED:
        description += " [APPROVAL REQUIRED]"

    return {
        "name": record.tool_name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": f"Input for {record.tool_name}",
                },
            },
        },
    }


def corpus_to_anthropic_schemas(
    entries: Sequence[CorpusEntry],
) -> list[dict]:
    """Convert the full MT-3 corpus into Anthropic tool schemas.

    Generated once, then subsetted by the benchmark.  Schema text is
    identical for any given tool regardless of which arm uses it.
    """
    schemas: list[dict] = []
    for entry in entries:
        d = entry.descriptor
        description = d.description or d.name
        if d.capability_flags & CapabilityFlags.AUTH_REQUIRED:
            description += " [APPROVAL REQUIRED]"

        schemas.append(
            {
                "name": d.name,
                "description": description,
                "input_schema": _infer_input_schema(d),
            }
        )
    return schemas
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_schema_bridge.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tcp/harness/schema_bridge.py tests/unit/test_schema_bridge.py
git commit -m "feat(harness): schema bridge — ToolRecord/CorpusEntry to Anthropic tool format"
```

---

### Task 3: Mock Executors

**Files:**
- Create: `tcp/agent/__init__.py`
- Create: `tcp/agent/mock_executors.py`
- Test: `tests/unit/test_mock_executors.py`

- [ ] **Step 1: Create the tcp/agent package**

```python
# tcp/agent/__init__.py
"""TCP agent experiment apparatus for EXP-2."""
```

- [ ] **Step 2: Write failing tests**

```python
# tests/unit/test_mock_executors.py
"""Tests for mock tool executors."""

from __future__ import annotations

import json

import pytest

from tcp.agent.mock_executors import MOCK_RESPONSES, get_mock_executor


class TestMockResponses:
    """Verify canned responses are valid JSON."""

    def test_all_responses_are_valid_json(self):
        for tool_name, response in MOCK_RESPONSES.items():
            parsed = json.loads(response)
            assert isinstance(parsed, dict), f"{tool_name} response is not a dict"

    def test_expected_tools_present(self):
        """Tools referenced by MT-3 tasks must have canned responses."""
        expected = {
            "fs-read-file",
            "jq",
            "git-status",
            "fs-search-files",
            "rag-query-documents",
            "git-commit",
            "chmod",
            "systemctl",
            "web-fetch",
            "oracle-execute-query",
        }
        missing = expected - set(MOCK_RESPONSES.keys())
        assert not missing, f"Missing mock responses for: {missing}"


class TestGetMockExecutor:
    """Verify the executor callable."""

    def test_returns_callable(self):
        executor = get_mock_executor()
        assert callable(executor)

    def test_known_tool_returns_canned_response(self):
        executor = get_mock_executor()
        result = executor("fs-read-file", {"input": "/tmp/test"})
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_unknown_tool_returns_default(self):
        executor = get_mock_executor()
        result = executor("nonexistent-tool-xyz", {})
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_response_is_string(self):
        executor = get_mock_executor()
        result = executor("jq", {"input": "test"})
        assert isinstance(result, str)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_mock_executors.py -v`
Expected: ImportError — `tcp.agent.mock_executors` does not exist.

- [ ] **Step 4: Implement mock executors**

```python
# tcp/agent/mock_executors.py
"""Canned tool responses for EXP-2 agent loop benchmarks.

Each tool in the MT-3 corpus has a canned JSON response.  The mock
returns valid-shaped JSON for the tool's declared output format,
isolating measurement to model-side behavior.
"""

from __future__ import annotations

from typing import Callable

MOCK_RESPONSES: dict[str, str] = {
    # --- Filesystem ---
    "fs-read-file": '{"content": "sample file content", "path": "/tmp/data.json"}',
    "fs-read-multiple": '{"files": [{"path": "/a.txt", "content": "a"}, {"path": "/b.txt", "content": "b"}]}',
    "fs-write-file": '{"status": "written", "bytes": 42}',
    "fs-edit-file": '{"status": "edited", "replacements": 1}',
    "fs-list-directory": '{"entries": ["file1.py", "file2.py", "dir1/"]}',
    "fs-directory-tree": '{"tree": {"name": "root", "children": [{"name": "src"}]}}',
    "fs-search-files": '{"matches": ["/src/main.py:10: TODO fix this"]}',
    "fs-get-file-info": '{"size": 1024, "modified": "2026-01-01T00:00:00Z"}',
    "fs-move-file": '{"status": "moved", "from": "/a", "to": "/b"}',
    "fs-create-directory": '{"status": "created", "path": "/new-dir"}',
    # --- Git ---
    "git-status": '{"branch": "main", "clean": true, "files": []}',
    "git-log": '{"commits": [{"sha": "abc123", "message": "initial"}]}',
    "git-diff": '{"diff": "--- a/file\\n+++ b/file\\n@@ -1 +1 @@"}',
    "git-diff-staged": '{"diff": ""}',
    "git-show": '{"sha": "abc123", "message": "initial", "diff": ""}',
    "git-commit": '{"sha": "def456", "message": "committed"}',
    "git-add": '{"staged": ["file.py"]}',
    "git-branch": '{"branches": ["main", "dev"], "current": "main"}',
    "git-checkout": '{"branch": "dev", "status": "switched"}',
    "git-reset": '{"status": "reset", "head": "abc123"}',
    # --- Notion ---
    "notion-chat-with-agent": '{"response": "Agent reply here"}',
    "notion-query-database": '{"results": [{"id": "page-1", "title": "Item"}]}',
    "notion-describe-database": '{"schema": {"Name": "title", "Status": "select"}}',
    "notion-create-agent": '{"id": "agent-1", "status": "created"}',
    "notion-update-agent": '{"id": "agent-1", "status": "updated"}',
    "notion-list-agents": '{"agents": [{"id": "agent-1", "name": "Bot"}]}',
    "notion-discover-agent": '{"agent": {"id": "agent-1", "capability": "chat"}}',
    "notion-dump-agent": '{"config": {"model": "claude", "tools": []}}',
    "notion-get-conversation": '{"messages": [{"role": "user", "content": "hi"}]}',
    "notion-handle-final-return": '{"status": "dispatched"}',
    # --- Playwright ---
    "browser-navigate": '{"url": "https://example.com", "status": 200}',
    "browser-click": '{"clicked": true, "selector": "#btn"}',
    "browser-fill-form": '{"filled": true, "fields": 2}',
    "browser-snapshot": '{"snapshot": "page title: Example"}',
    "browser-screenshot": '{"path": "/tmp/screenshot.png"}',
    "browser-evaluate": '{"result": "42"}',
    "browser-press-key": '{"key": "Enter", "sent": true}',
    "browser-tabs": '{"tabs": [{"id": 1, "url": "https://example.com"}]}',
    # --- Fetch / Network ---
    "web-fetch": '{"status": 200, "body": "<html>example</html>"}',
    # --- Exa ---
    "exa-web-search": '{"results": [{"title": "Result", "url": "https://example.com"}]}',
    "exa-company-research": '{"company": "Acme", "info": "Founded 2020"}',
    "exa-code-context": '{"context": "function main() {}"}',
    # --- Context7 ---
    "c7-resolve-library": '{"library_id": "/react/latest", "version": "19.0"}',
    "c7-query-docs": '{"content": "React.useState hook documentation..."}',
    # --- Oracle ---
    "oracle-execute-query": '{"rows": [{"id": 1, "name": "Alice"}], "count": 1}',
    "oracle-describe-table": '{"columns": [{"name": "id", "type": "NUMBER"}]}',
    "oracle-list-tables": '{"tables": ["users", "orders"]}',
    "oracle-list-schemas": '{"schemas": ["HR", "SALES"]}',
    "oracle-get-table-indexes": '{"indexes": [{"name": "pk_users"}]}',
    "oracle-get-table-constraints": '{"constraints": [{"name": "pk_users", "type": "PRIMARY KEY"}]}',
    # --- Gmail ---
    "gmail-search": '{"messages": [{"id": "msg-1", "subject": "Hello"}]}',
    "gmail-read": '{"id": "msg-1", "body": "Message body"}',
    "gmail-list-labels": '{"labels": ["INBOX", "SENT"]}',
    "gmail-profile": '{"email": "user@example.com"}',
    "gmail-create-draft": '{"id": "draft-1", "status": "created"}',
    "gmail-list-drafts": '{"drafts": [{"id": "draft-1"}]}',
    "gmail-read-thread": '{"messages": [{"id": "msg-1"}]}',
    # --- Google Calendar ---
    "gcal-list-events": '{"events": [{"summary": "Meeting", "start": "10:00"}]}',
    "gcal-create-event": '{"id": "evt-1", "status": "created"}',
    "gcal-get-event": '{"id": "evt-1", "summary": "Meeting"}',
    "gcal-update-event": '{"id": "evt-1", "status": "updated"}',
    "gcal-delete-event": '{"status": "deleted"}',
    "gcal-list-calendars": '{"calendars": [{"id": "primary", "name": "Main"}]}',
    "gcal-find-free-time": '{"slots": [{"start": "14:00", "end": "15:00"}]}',
    "gcal-find-meeting-times": '{"times": [{"start": "14:00", "end": "15:00"}]}',
    "gcal-respond-to-event": '{"status": "accepted"}',
    # --- Vercel ---
    "vercel-list-projects": '{"projects": [{"name": "my-app"}]}',
    "vercel-get-project": '{"name": "my-app", "framework": "next"}',
    "vercel-deploy": '{"id": "dpl-1", "url": "https://my-app.vercel.app"}',
    "vercel-list-deployments": '{"deployments": [{"id": "dpl-1"}]}',
    "vercel-get-deployment": '{"id": "dpl-1", "state": "READY"}',
    "vercel-build-logs": '{"logs": ["Building...", "Done"]}',
    "vercel-runtime-logs": '{"logs": []}',
    "vercel-search-docs": '{"results": [{"title": "Deployment"}]}',
    # --- Tally ---
    "tally-list-forms": '{"forms": [{"id": "form-1", "title": "Survey"}]}',
    "tally-create-form": '{"id": "form-1", "status": "created"}',
    "tally-load-form": '{"id": "form-1", "blocks": []}',
    "tally-save-form": '{"status": "saved"}',
    "tally-submissions": '{"submissions": [{"id": "sub-1"}]}',
    # --- Writing RAG ---
    "rag-query-documents": '{"results": [{"text": "matching passage", "score": 0.2}]}',
    "rag-query-passages": '{"passages": [{"text": "passage", "score": 0.3}]}',
    "rag-list-files": '{"files": ["essay.md", "notes.md"]}',
    "rag-ingest-file": '{"status": "ingested", "chunks": 12}',
    "rag-ingest-data": '{"status": "ingested", "chunks": 5}',
    "rag-status": '{"total_files": 10, "total_chunks": 120}',
    "rag-get-passage": '{"text": "The passage content here"}',
    "rag-delete-file": '{"status": "deleted"}',
    # --- NixOS ---
    "nix-search": '{"packages": [{"name": "hello", "version": "2.12"}]}',
    "nix-versions": '{"versions": ["24.05", "24.11"]}',
    # --- System commands ---
    "jq": '{"result": {"name": "extracted_value"}}',
    "chmod": '{"status": "permissions changed", "mode": "644"}',
    "systemctl": '{"status": "active", "service": "nginx"}',
    "curl": '{"status": 200, "body": "response"}',
}

_DEFAULT_RESPONSE = '{"status": "ok"}'


def get_mock_executor() -> Callable[[str, dict], str]:
    """Return a mock executor that returns canned JSON responses.

    Unknown tools get a generic ``{"status": "ok"}`` response.
    """

    def _execute(tool_name: str, tool_input: dict) -> str:  # noqa: ARG001
        return MOCK_RESPONSES.get(tool_name, _DEFAULT_RESPONSE)

    return _execute
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_mock_executors.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tcp/agent/__init__.py tcp/agent/mock_executors.py tests/unit/test_mock_executors.py
git commit -m "feat(agent): mock executors with canned responses for MT-3 corpus"
```

---

### Task 4: Agent Task Definitions

**Files:**
- Create: `tcp/agent/tasks.py`
- Test: `tests/unit/test_agent_tasks.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_agent_tasks.py
"""Tests for EXP-2 agent task definitions."""

from __future__ import annotations

import pytest

from tcp.agent.tasks import AgentTask, build_agent_tasks


class TestAgentTask:
    """Verify AgentTask structure."""

    def test_frozen(self):
        task = AgentTask(
            name="test",
            prompt="Do something",
            expected_tool="some-tool",
        )
        with pytest.raises(AttributeError):
            task.name = "changed"  # type: ignore[misc]


class TestBuildAgentTasks:
    """Verify the 12 MT-3 agent tasks."""

    def test_returns_12_tasks(self):
        tasks = build_agent_tasks()
        assert len(tasks) == 12

    def test_all_have_prompts(self):
        tasks = build_agent_tasks()
        for t in tasks:
            assert isinstance(t.prompt, str)
            assert len(t.prompt) > 10, f"Task {t.name!r} has too short a prompt"

    def test_all_have_names(self):
        tasks = build_agent_tasks()
        names = [t.name for t in tasks]
        assert len(set(names)) == 12, "Task names must be unique"

    def test_expected_tools_match_mt3(self):
        """Expected tools align with MT-3 benchmark task expectations."""
        tasks = build_agent_tasks()
        by_name = {t.name: t for t in tasks}

        assert by_name["local file read"].expected_tool == "fs-read-file"
        assert by_name["local json processing"].expected_tool == "jq"
        assert by_name["git status check"].expected_tool == "git-status"
        assert by_name["file search"].expected_tool == "fs-search-files"
        assert by_name["semantic document search"].expected_tool == "rag-query-documents"
        assert by_name["git commit (write)"].expected_tool == "git-commit"
        # These tasks expect no tool to be auto-approved
        assert by_name["approval-guarded privileged command"].expected_tool == "chmod"
        assert by_name["approval-guarded systemctl"].expected_tool == "systemctl"
        assert by_name["network fetch denied offline"].expected_tool is None
        assert by_name["database query denied offline"].expected_tool is None
        assert by_name["nonexistent command"].expected_tool is None
        assert by_name["require JSON output"].expected_tool == "jq"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_agent_tasks.py -v`
Expected: ImportError — `tcp.agent.tasks` does not exist.

- [ ] **Step 3: Implement agent tasks**

```python
# tcp/agent/tasks.py
"""EXP-2 agent task definitions with natural language prompts.

Each task maps to one of the 12 MT-3 benchmark tasks but adds a natural
language prompt for the agent loop and an expected tool name for
correctness checking.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTask:
    """A single agent loop task with natural language prompt."""

    name: str
    prompt: str
    expected_tool: str | None  # None when no tool should be selected


def build_agent_tasks() -> list[AgentTask]:
    """Build the 12 EXP-2 agent tasks aligned with MT-3 benchmarks."""
    return [
        # --- Offline local tasks ---
        AgentTask(
            name="local file read",
            prompt="Read the file at /tmp/data.json and show me its contents.",
            expected_tool="fs-read-file",
        ),
        AgentTask(
            name="local json processing",
            prompt=(
                "Use jq to extract the 'name' field from the JSON file "
                "at /tmp/data.json."
            ),
            expected_tool="jq",
        ),
        AgentTask(
            name="git status check",
            prompt="Show me the current git status of the repository.",
            expected_tool="git-status",
        ),
        AgentTask(
            name="file search",
            prompt="Search for all files containing 'TODO' in the src directory.",
            expected_tool="fs-search-files",
        ),
        AgentTask(
            name="semantic document search",
            prompt=(
                "Find documents in the knowledge base related to "
                "authentication and session management."
            ),
            expected_tool="rag-query-documents",
        ),
        AgentTask(
            name="git commit (write)",
            prompt="Commit the currently staged changes with the message 'fix: resolve auth bug'.",
            expected_tool="git-commit",
        ),
        # --- Approval-gated tasks ---
        AgentTask(
            name="approval-guarded privileged command",
            prompt="Change the permissions of /etc/config.json to 644.",
            expected_tool="chmod",
        ),
        AgentTask(
            name="approval-guarded systemctl",
            prompt="Restart the nginx service using systemctl.",
            expected_tool="systemctl",
        ),
        # --- Network tasks (should fail in offline env) ---
        AgentTask(
            name="network fetch denied offline",
            prompt="Fetch the contents of https://api.example.com/data.",
            expected_tool=None,  # network tools denied in offline env
        ),
        AgentTask(
            name="database query denied offline",
            prompt="Run the SQL query: SELECT * FROM users LIMIT 10.",
            expected_tool=None,  # network tools denied in offline env
        ),
        # --- No-match tasks ---
        AgentTask(
            name="nonexistent command",
            prompt="Teleport the quantum state to the remote server.",
            expected_tool=None,  # no tool matches
        ),
        # --- Capability-flag tasks ---
        AgentTask(
            name="require JSON output",
            prompt="Convert the input data to well-formatted JSON output.",
            expected_tool="jq",
        ),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_agent_tasks.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tcp/agent/tasks.py tests/unit/test_agent_tasks.py
git commit -m "feat(agent): 12 agent task definitions with NL prompts for EXP-2"
```

---

### Task 5: Agent Loop

**Files:**
- Create: `tcp/agent/loop.py`
- Test: `tests/unit/test_agent_loop.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_agent_loop.py
"""Tests for the instrumented agent loop.

All tests mock the Anthropic API — no real API calls are made.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tcp.agent.loop import LoopMetrics, run_agent_loop


# --- Test fixtures ---


def _make_usage(input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    return usage


def _make_text_block(text: str = "Done.") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(
    name: str = "fs-read-file",
    tool_input: dict | None = None,
    tool_id: str = "toolu_123",
) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = tool_input or {"input": "test"}
    block.id = tool_id
    return block


def _make_response(
    content: list,
    input_tokens: int = 100,
    output_tokens: int = 50,
    stop_reason: str = "end_turn",
) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.usage = _make_usage(input_tokens, output_tokens)
    resp.stop_reason = stop_reason
    return resp


def _noop_executor(tool_name: str, tool_input: dict) -> str:
    return '{"status": "ok"}'


# --- Tests ---


class TestLoopMetrics:
    """LoopMetrics is a frozen dataclass."""

    def test_frozen(self):
        m = LoopMetrics(
            task_name="test",
            tool_count=10,
            turns=1,
            first_token_latency_ms=50.0,
            total_response_time_ms=100.0,
            input_tokens=200,
            output_tokens=50,
            tools_called=("fs-read-file",),
            selected_tool_correct=True,
            error=None,
        )
        with pytest.raises(AttributeError):
            m.task_name = "changed"  # type: ignore[misc]

    def test_fields(self):
        m = LoopMetrics(
            task_name="t",
            tool_count=5,
            turns=2,
            first_token_latency_ms=10.0,
            total_response_time_ms=20.0,
            input_tokens=100,
            output_tokens=50,
            tools_called=("a", "b"),
            selected_tool_correct=True,
            error=None,
        )
        assert m.turns == 2
        assert m.tools_called == ("a", "b")


@pytest.mark.asyncio
class TestRunAgentLoop:
    """Test the agent loop with mocked Anthropic API."""

    async def test_single_turn_no_tool_use(self):
        """Model responds with text only — no tool calls."""
        mock_response = _make_response(
            content=[_make_text_block("I can help with that.")],
            input_tokens=150,
            output_tokens=30,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Hello",
                tools=[{"name": "test", "description": "t", "input_schema": {"type": "object", "properties": {}}}],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="test-task",
            )

        assert metrics.turns == 1
        assert metrics.tools_called == ()
        assert metrics.input_tokens == 150
        assert metrics.output_tokens == 30
        assert metrics.first_token_latency_ms > 0
        assert metrics.total_response_time_ms >= metrics.first_token_latency_ms
        assert metrics.selected_tool_correct is True  # no tool expected, none called
        assert metrics.error is None

    async def test_single_tool_call(self):
        """Model calls one tool then finishes."""
        tool_response = _make_response(
            content=[_make_tool_use_block("fs-read-file", {"input": "/tmp/x"})],
            input_tokens=200,
            output_tokens=40,
        )
        final_response = _make_response(
            content=[_make_text_block("Here are the contents.")],
            input_tokens=250,
            output_tokens=20,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Read /tmp/x",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="read-test",
            )

        assert metrics.turns == 2
        assert metrics.tools_called == ("fs-read-file",)
        assert metrics.input_tokens == 450  # 200 + 250
        assert metrics.output_tokens == 60  # 40 + 20
        assert metrics.selected_tool_correct is True

    async def test_wrong_tool_selected(self):
        """Model calls a different tool than expected."""
        tool_response = _make_response(
            content=[_make_tool_use_block("git-status")],
            input_tokens=100,
            output_tokens=30,
        )
        final_response = _make_response(
            content=[_make_text_block("Done.")],
            input_tokens=120,
            output_tokens=10,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Read file",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="wrong-tool",
            )

        assert metrics.selected_tool_correct is False
        assert metrics.tools_called == ("git-status",)

    async def test_max_turns_respected(self):
        """Loop stops after max_turns even if model keeps calling tools."""
        tool_response = _make_response(
            content=[_make_tool_use_block("fs-read-file")],
            input_tokens=100,
            output_tokens=20,
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=tool_response)

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Loop forever",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool="fs-read-file",
                task_name="max-turns",
                max_turns=3,
            )

        assert metrics.turns == 3

    async def test_api_error_captured(self):
        """API errors are captured in the error field, not raised."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=Exception("rate limited")
        )

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Fail",
                tools=[],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="error-test",
            )

        assert metrics.error is not None
        assert "rate limited" in metrics.error
        assert metrics.turns == 0

    async def test_tool_count_from_tools_list(self):
        """tool_count reflects the number of tools provided."""
        mock_response = _make_response(content=[_make_text_block("ok")])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        tools = [
            {"name": f"tool-{i}", "description": f"t{i}", "input_schema": {"type": "object", "properties": {}}}
            for i in range(15)
        ]

        with patch("tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client):
            metrics = await run_agent_loop(
                task_prompt="Hi",
                tools=tools,
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="count-test",
            )

        assert metrics.tool_count == 15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_agent_loop.py -v`
Expected: ImportError — `tcp.agent.loop` does not exist.

- [ ] **Step 3: Implement agent loop**

```python
# tcp/agent/loop.py
"""Instrumented async agent loop for EXP-2 benchmarking.

Executes a single task against the Anthropic Messages API, collects
timing and token metrics at every API call boundary, and dispatches
tool calls to a mock executor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import anthropic


@dataclass(frozen=True)
class LoopMetrics:
    """Timing and correctness metrics from a single agent loop run."""

    task_name: str
    tool_count: int
    turns: int
    first_token_latency_ms: float
    total_response_time_ms: float
    input_tokens: int
    output_tokens: int
    tools_called: tuple[str, ...]
    selected_tool_correct: bool
    error: str | None


async def run_agent_loop(
    task_prompt: str,
    tools: list[dict],
    mock_executor: Callable[[str, dict], str],
    *,
    expected_tool: str | None,
    task_name: str,
    model: str = "claude-sonnet-4-6",
    max_turns: int = 5,
) -> LoopMetrics:
    """Execute a single agent loop and return metrics.

    1. Call messages.create() with task_prompt and tools
    2. If response contains tool_use, dispatch to mock_executor
    3. Feed tool_result back, repeat until text-only or max_turns
    4. Collect timing at every API call boundary
    """
    client = anthropic.AsyncAnthropic()
    messages: list[dict] = [{"role": "user", "content": task_prompt}]
    tools_called: list[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    first_token_latency_ms = 0.0
    turns = 0

    total_start = time.perf_counter_ns()

    try:
        for turn_idx in range(max_turns):
            call_start = time.perf_counter_ns()
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                messages=messages,
                tools=tools,
            )
            call_end = time.perf_counter_ns()

            turns += 1

            if turn_idx == 0:
                first_token_latency_ms = (call_end - call_start) / 1_000_000

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Extract tool_use blocks
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                break

            # Dispatch each tool call to mock executor
            tool_results = []
            for block in tool_use_blocks:
                tools_called.append(block.name)
                result = mock_executor(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

    except Exception as exc:
        total_end = time.perf_counter_ns()
        return LoopMetrics(
            task_name=task_name,
            tool_count=len(tools),
            turns=turns,
            first_token_latency_ms=first_token_latency_ms,
            total_response_time_ms=(total_end - total_start) / 1_000_000,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            tools_called=tuple(tools_called),
            selected_tool_correct=False,
            error=str(exc),
        )

    total_end = time.perf_counter_ns()

    # Correctness: check if the first tool called matches expected
    first_tool = tools_called[0] if tools_called else None
    if expected_tool is None:
        correct = first_tool is None
    else:
        correct = first_tool == expected_tool

    return LoopMetrics(
        task_name=task_name,
        tool_count=len(tools),
        turns=turns,
        first_token_latency_ms=first_token_latency_ms,
        total_response_time_ms=(total_end - total_start) / 1_000_000,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        tools_called=tuple(tools_called),
        selected_tool_correct=correct,
        error=None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_agent_loop.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tcp/agent/loop.py tests/unit/test_agent_loop.py
git commit -m "feat(agent): instrumented async agent loop with timing + token metrics"
```

---

### Task 6: Benchmark Runner

**Files:**
- Create: `tcp/agent/benchmark.py`
- Test: `tests/unit/test_agent_benchmark.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_agent_benchmark.py
"""Tests for the paired benchmark runner.

All tests mock run_agent_loop — no real API calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tcp.agent.benchmark import (
    BenchmarkReport,
    PairedTrial,
    build_filtered_schemas,
    run_paired_benchmark,
)
from tcp.agent.loop import LoopMetrics
from tcp.agent.tasks import AgentTask


def _make_metrics(
    task_name: str = "test",
    tool_count: int = 90,
    turns: int = 2,
    first_token_latency_ms: float = 100.0,
    total_response_time_ms: float = 200.0,
    input_tokens: int = 500,
    output_tokens: int = 100,
    tools_called: tuple[str, ...] = ("fs-read-file",),
    selected_tool_correct: bool = True,
    error: str | None = None,
) -> LoopMetrics:
    return LoopMetrics(
        task_name=task_name,
        tool_count=tool_count,
        turns=turns,
        first_token_latency_ms=first_token_latency_ms,
        total_response_time_ms=total_response_time_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tools_called=tools_called,
        selected_tool_correct=selected_tool_correct,
        error=error,
    )


class TestPairedTrial:
    """PairedTrial delta properties."""

    def test_latency_delta(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(total_response_time_ms=300.0),
            filtered=_make_metrics(total_response_time_ms=200.0),
        )
        assert trial.latency_delta_ms == pytest.approx(100.0)

    def test_token_delta(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(input_tokens=1000),
            filtered=_make_metrics(input_tokens=400),
        )
        assert trial.token_delta == 600

    def test_frozen(self):
        trial = PairedTrial(
            task_name="t",
            unfiltered=_make_metrics(),
            filtered=_make_metrics(),
        )
        with pytest.raises(AttributeError):
            trial.task_name = "changed"  # type: ignore[misc]


class TestBuildFilteredSchemas:
    """Test filtered schema generation from corpus + gating."""

    def test_returns_dict_keyed_by_task_name(self):
        from tcp.agent.tasks import build_agent_tasks

        tasks = build_agent_tasks()
        corpus_schemas = [
            {"name": f"tool-{i}", "description": f"t{i}", "input_schema": {"type": "object", "properties": {}}}
            for i in range(10)
        ]
        # With dummy schemas, filtered output should be a dict
        filtered = build_filtered_schemas(tasks, corpus_schemas)
        assert isinstance(filtered, dict)
        assert set(filtered.keys()) == {t.name for t in tasks}

    def test_filtered_is_subset_of_corpus(self):
        from tcp.agent.tasks import build_agent_tasks

        tasks = build_agent_tasks()
        corpus_schemas = [
            {"name": f"tool-{i}", "description": f"t{i}", "input_schema": {"type": "object", "properties": {}}}
            for i in range(10)
        ]
        corpus_names = {s["name"] for s in corpus_schemas}
        filtered = build_filtered_schemas(tasks, corpus_schemas)
        for task_name, schemas in filtered.items():
            for s in schemas:
                assert s["name"] in corpus_names, (
                    f"Filtered schema {s['name']} for task {task_name!r} "
                    f"not in corpus"
                )

    def test_mt3_corpus_filtering(self):
        """With real MT-3 corpus, filtered sets are smaller than unfiltered."""
        from tcp.agent.tasks import build_agent_tasks
        from tcp.harness.corpus import build_mcp_corpus
        from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

        entries = build_mcp_corpus()
        corpus_schemas = corpus_to_anthropic_schemas(entries)
        tasks = build_agent_tasks()
        filtered = build_filtered_schemas(tasks, corpus_schemas)

        # At least some tasks should have fewer tools than the full corpus
        full_count = len(corpus_schemas)
        any_reduced = any(
            len(schemas) < full_count for schemas in filtered.values()
        )
        assert any_reduced, "No task had a reduced tool set after filtering"


class TestBenchmarkReport:
    """BenchmarkReport summary computation."""

    def test_summary_keys(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(
                input_tokens=1000,
                total_response_time_ms=300.0,
                selected_tool_correct=True,
            ),
            filtered=_make_metrics(
                input_tokens=400,
                total_response_time_ms=200.0,
                selected_tool_correct=True,
            ),
        )
        report = BenchmarkReport.from_trials([trial])
        assert "mean_latency_delta_ms" in report.summary
        assert "mean_token_delta" in report.summary
        assert "filtered_correct_rate" in report.summary
        assert "unfiltered_correct_rate" in report.summary
        assert "trial_count" in report.summary

    def test_summary_values(self):
        trial = PairedTrial(
            task_name="test",
            unfiltered=_make_metrics(
                input_tokens=1000,
                total_response_time_ms=300.0,
                selected_tool_correct=True,
            ),
            filtered=_make_metrics(
                input_tokens=400,
                total_response_time_ms=200.0,
                selected_tool_correct=True,
            ),
        )
        report = BenchmarkReport.from_trials([trial])
        assert report.summary["mean_latency_delta_ms"] == pytest.approx(100.0)
        assert report.summary["mean_token_delta"] == 600
        assert report.summary["filtered_correct_rate"] == pytest.approx(1.0)
        assert report.summary["trial_count"] == 1


@pytest.mark.asyncio
class TestRunPairedBenchmark:
    """Test the full paired benchmark runner with mocked loop."""

    async def test_runs_correct_number_of_trials(self):
        tasks = [
            AgentTask(name="task-a", prompt="Do A", expected_tool="tool-a"),
            AgentTask(name="task-b", prompt="Do B", expected_tool="tool-b"),
        ]
        corpus_schemas = [
            {"name": "tool-a", "description": "A", "input_schema": {"type": "object", "properties": {}}},
            {"name": "tool-b", "description": "B", "input_schema": {"type": "object", "properties": {}}},
        ]
        filtered = {
            "task-a": [corpus_schemas[0]],
            "task-b": [corpus_schemas[1]],
        }

        call_count = 0

        async def mock_loop(task_prompt, tools, mock_executor, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_metrics(
                task_name=kwargs.get("task_name", "test"),
                tool_count=len(tools),
            )

        with patch("tcp.agent.benchmark.run_agent_loop", side_effect=mock_loop):
            report = await run_paired_benchmark(
                tasks=tasks,
                corpus_schemas=corpus_schemas,
                filtered_schemas_by_task=filtered,
                repetitions=3,
            )

        # 2 tasks x 3 reps x 2 arms = 12 loop calls
        assert call_count == 12
        assert len(report.trials) == 6  # 2 tasks x 3 reps

    async def test_report_has_summary(self):
        tasks = [AgentTask(name="t", prompt="P", expected_tool="x")]
        schemas = [{"name": "x", "description": "X", "input_schema": {"type": "object", "properties": {}}}]
        filtered = {"t": schemas}

        async def mock_loop(task_prompt, tools, mock_executor, **kwargs):
            return _make_metrics(task_name="t", tool_count=len(tools))

        with patch("tcp.agent.benchmark.run_agent_loop", side_effect=mock_loop):
            report = await run_paired_benchmark(
                tasks=tasks,
                corpus_schemas=schemas,
                filtered_schemas_by_task=filtered,
                repetitions=1,
            )

        assert isinstance(report.summary, dict)
        assert report.summary["trial_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_agent_benchmark.py -v`
Expected: ImportError — `tcp.agent.benchmark` does not exist.

- [ ] **Step 3: Implement benchmark runner**

```python
# tcp/agent/benchmark.py
"""Paired benchmark runner for EXP-2.

Runs filtered/unfiltered trials with randomized ordering to control for
prompt-cache state and network conditions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from tcp.agent.loop import LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.agent.tasks import AgentTask


@dataclass(frozen=True)
class PairedTrial:
    """One paired filtered/unfiltered comparison for a single task."""

    task_name: str
    unfiltered: LoopMetrics
    filtered: LoopMetrics

    @property
    def latency_delta_ms(self) -> float:
        """Positive means unfiltered was slower (filtered wins)."""
        return self.unfiltered.total_response_time_ms - self.filtered.total_response_time_ms

    @property
    def token_delta(self) -> int:
        """Positive means unfiltered used more tokens (filtered wins)."""
        return self.unfiltered.input_tokens - self.filtered.input_tokens


@dataclass(frozen=True)
class BenchmarkReport:
    """Complete benchmark report with per-trial data and summary."""

    trials: tuple[PairedTrial, ...]
    summary: dict[str, float | int]

    @classmethod
    def from_trials(cls, trials: list[PairedTrial]) -> BenchmarkReport:
        """Compute summary statistics from a list of trials."""
        if not trials:
            return cls(trials=(), summary={"trial_count": 0})

        latency_deltas = [t.latency_delta_ms for t in trials]
        token_deltas = [t.token_delta for t in trials]
        filtered_correct = sum(1 for t in trials if t.filtered.selected_tool_correct)
        unfiltered_correct = sum(1 for t in trials if t.unfiltered.selected_tool_correct)
        n = len(trials)

        return cls(
            trials=tuple(trials),
            summary={
                "trial_count": n,
                "mean_latency_delta_ms": sum(latency_deltas) / n,
                "mean_token_delta": sum(token_deltas) / n,
                "min_latency_delta_ms": min(latency_deltas),
                "max_latency_delta_ms": max(latency_deltas),
                "filtered_correct_rate": filtered_correct / n,
                "unfiltered_correct_rate": unfiltered_correct / n,
                "total_filtered_input_tokens": sum(
                    t.filtered.input_tokens for t in trials
                ),
                "total_unfiltered_input_tokens": sum(
                    t.unfiltered.input_tokens for t in trials
                ),
            },
        )


def build_filtered_schemas(
    tasks: list[AgentTask],
    corpus_schemas: list[dict],
) -> dict[str, list[dict]]:
    """Build per-task filtered schema subsets using TCP gating.

    Uses the MT-3 offline environment (network denied) with bitmask
    filtering.  Each task gets the bitmask survivors as its filtered set.
    """
    from tcp.core.descriptors import CapabilityFlags
    from tcp.harness.bitmask_filter import EnvironmentMask, bitmask_filter
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.normalize import normalize_capability_descriptor

    # Build ToolRecords from corpus
    entries = build_mcp_corpus()
    records = []
    for entry in entries:
        records.append(normalize_capability_descriptor(entry.descriptor))

    # Build schema lookup by tool name
    schema_by_name: dict[str, dict] = {s["name"]: s for s in corpus_schemas}

    # Offline environment: deny network tools
    deny = EnvironmentMask.from_constraints(network=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    result = bitmask_filter(records, deny_mask=deny, approval_mask=approval)
    survivor_names = frozenset(r.tool_name for r in result.survivors)

    # Every task gets the same bitmask-filtered set (offline environment)
    filtered_schemas = [
        schema_by_name[name]
        for name in survivor_names
        if name in schema_by_name
    ]

    return {task.name: filtered_schemas for task in tasks}


async def run_paired_benchmark(
    tasks: list[AgentTask],
    corpus_schemas: list[dict],
    filtered_schemas_by_task: dict[str, list[dict]],
    *,
    repetitions: int = 5,
    model: str = "claude-sonnet-4-6",
) -> BenchmarkReport:
    """Run paired filtered/unfiltered trials for each task.

    Order is randomized per pair to control for prompt caching.
    """
    mock_exec = get_mock_executor()
    all_trials: list[PairedTrial] = []

    for task in tasks:
        filtered_schemas = filtered_schemas_by_task[task.name]

        for _rep in range(repetitions):
            # Randomize which arm runs first
            run_filtered_first = random.random() < 0.5

            async def _run_arm(schemas: list[dict]) -> LoopMetrics:
                return await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                )

            if run_filtered_first:
                filtered_metrics = await _run_arm(filtered_schemas)
                unfiltered_metrics = await _run_arm(corpus_schemas)
            else:
                unfiltered_metrics = await _run_arm(corpus_schemas)
                filtered_metrics = await _run_arm(filtered_schemas)

            all_trials.append(
                PairedTrial(
                    task_name=task.name,
                    unfiltered=unfiltered_metrics,
                    filtered=filtered_metrics,
                )
            )

    return BenchmarkReport.from_trials(all_trials)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_agent_benchmark.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tcp/agent/benchmark.py tests/unit/test_agent_benchmark.py
git commit -m "feat(agent): paired benchmark runner with randomized trial ordering"
```

---

### Task 7: Package Wiring and Exports

**Files:**
- Modify: `tcp/agent/__init__.py`
- Modify: `tcp/harness/__init__.py` (add schema_bridge exports)

- [ ] **Step 1: Update tcp/agent/__init__.py with public exports**

```python
# tcp/agent/__init__.py
"""TCP agent experiment apparatus for EXP-2.

Instrumented agent loop, mock executors, and paired benchmark runner
for measuring TCP pre-filtering effects on Claude's tool selection
and latency.
"""

from tcp.agent.benchmark import BenchmarkReport, PairedTrial, run_paired_benchmark
from tcp.agent.loop import LoopMetrics, run_agent_loop
from tcp.agent.mock_executors import get_mock_executor
from tcp.agent.tasks import AgentTask, build_agent_tasks

__all__ = [
    "AgentTask",
    "BenchmarkReport",
    "LoopMetrics",
    "PairedTrial",
    "build_agent_tasks",
    "get_mock_executor",
    "run_agent_loop",
    "run_paired_benchmark",
]
```

- [ ] **Step 2: Add schema_bridge to tcp/harness/__init__.py**

Add these imports and exports to the existing `tcp/harness/__init__.py`:

```python
# Add to imports
from tcp.harness.schema_bridge import (
    corpus_to_anthropic_schemas,
    tool_record_to_anthropic_schema,
)

# Add to __all__
"corpus_to_anthropic_schemas",
"tool_record_to_anthropic_schema",
```

- [ ] **Step 3: Run full test suite**

Run: `poetry run pytest tests/unit/test_schema_bridge.py tests/unit/test_mock_executors.py tests/unit/test_agent_tasks.py tests/unit/test_agent_loop.py tests/unit/test_agent_benchmark.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tcp/agent/__init__.py tcp/harness/__init__.py
git commit -m "feat(agent): wire up package exports for EXP-2 agent apparatus"
```

---

## Spec Coverage Verification

| Spec requirement | Task |
|-----------------|------|
| Schema bridge (`schema_bridge.py`) | Task 2 |
| Schema parity invariant | Task 2 (test: `test_schema_parity_invariant`) |
| AUTH_REQUIRED description annotation | Task 2 (test: `test_auth_required_annotation`) |
| Agent loop (`loop.py`) with timing instrumentation | Task 5 |
| `time.perf_counter_ns()` around each API call | Task 5 (`loop.py` implementation) |
| Token counts from API response `usage` | Task 5 |
| Non-streaming calls, first-token = first call wall-clock | Task 5 |
| Tool name extraction from `tool_use` blocks | Task 5 |
| Mock executors with canned JSON responses | Task 3 |
| `LoopMetrics` dataclass with all specified fields | Task 5 |
| `PairedTrial` with delta properties | Task 6 |
| `BenchmarkReport` with summary | Task 6 |
| Randomized order per pair | Task 6 (`run_paired_benchmark`) |
| 12 MT-3 task reuse with NL prompts | Task 4 |
| Schema bridge coverage >= 80% of corpus | Task 2 (test: `test_mt3_corpus_coverage`) |
| `anthropic` SDK dependency | Task 1 |
| File layout: `tcp/agent/`, `tcp/harness/schema_bridge.py` | All tasks |
| `claude-sonnet-4-6` model default | Task 5, Task 6 |
| `max_turns` parameter | Task 5 |
| Error captured in metrics, not raised | Task 5 (test: `test_api_error_captured`) |
