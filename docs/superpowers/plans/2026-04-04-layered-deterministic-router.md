# Layered Deterministic Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route deterministically when TCP filtering yields exactly 1 tool (bypassing the LLM), involve the LLM only when 2+ tools survive, and prove the LLM earns its keep on ambiguous tasks.

**Architecture:** Add `RouteConfidence` to the existing `RouteResult` dataclass. The agent loop branches on confidence: DETERMINISTIC skips the API call and invokes the mock executor directly; AMBIGUOUS sends the filtered set to the LLM as today. A new ambiguous task corpus produces 2-5 survivors per task to stress-test LLM judgment. Three-lane benchmark reporting breaks out deterministic/ambiguous/no-match metrics.

**Tech Stack:** Python 3.8+, pytest, pytest-asyncio, anthropic SDK (mocked in tests), existing TCP harness infrastructure.

---

### Task 1: Add RouteConfidence to router.py

**Files:**
- Modify: `tcp/harness/router.py:10-11` (imports), `tcp/harness/router.py:21-31` (RouteResult)
- Test: `tests/unit/test_router.py` (new)

- [ ] **Step 1: Write failing tests for RouteConfidence**

Create `tests/unit/test_router.py`:

```python
"""Tests for RouteConfidence and the layered router split."""

from __future__ import annotations

import pytest

from tcp.harness.router import RouteConfidence, RouteResult, route_tool
from tcp.harness.gating import RuntimeEnvironment
from tcp.harness.models import ToolRecord, ToolSelectionRequest


def _make_record(name: str, commands: frozenset[str] = frozenset()) -> ToolRecord:
    return ToolRecord(
        tool_name=name,
        descriptor_source="test",
        descriptor_version="1.0",
        capability_flags=0,
        risk_level="safe",
        commands=commands,
    )


def _make_env() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        network_enabled=False,
        file_access_enabled=True,
        stdin_enabled=True,
        installed_tools=frozenset(),
    )


class TestRouteConfidence:
    """RouteConfidence enum values."""

    def test_enum_values(self):
        assert RouteConfidence.DETERMINISTIC.value == "deterministic"
        assert RouteConfidence.AMBIGUOUS.value == "ambiguous"
        assert RouteConfidence.NO_MATCH.value == "no_match"


class TestRouteResultConfidence:
    """route_tool sets confidence based on survivor count."""

    def test_deterministic_when_one_survivor(self):
        tools = [_make_record("only-tool", commands=frozenset({"do_thing"}))]
        request = ToolSelectionRequest.from_kwargs(
            required_commands={"do_thing"},
            require_auto_approval=False,
        )
        result = route_tool(tools, request, _make_env())
        assert result.confidence == RouteConfidence.DETERMINISTIC
        assert result.survivor_count == 1
        assert result.selected_tool is not None

    def test_ambiguous_when_multiple_survivors(self):
        tools = [
            _make_record("tool-a"),
            _make_record("tool-b"),
        ]
        request = ToolSelectionRequest.from_kwargs(
            preferred_criteria="speed",
            require_auto_approval=False,
        )
        result = route_tool(tools, request, _make_env())
        assert result.confidence == RouteConfidence.AMBIGUOUS
        assert result.survivor_count == 2

    def test_no_match_when_zero_survivors(self):
        tools = [_make_record("tool-a", commands=frozenset({"x"}))]
        request = ToolSelectionRequest.from_kwargs(
            required_commands={"nonexistent"},
            require_auto_approval=False,
        )
        result = route_tool(tools, request, _make_env())
        assert result.confidence == RouteConfidence.NO_MATCH
        assert result.survivor_count == 0

    def test_candidate_scores_none_by_default(self):
        tools = [_make_record("t")]
        request = ToolSelectionRequest.from_kwargs(require_auto_approval=False)
        result = route_tool(tools, request, _make_env())
        assert result.candidate_scores is None
        assert result.score_gap is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_router.py -v`
Expected: FAIL — `RouteConfidence` not found, `confidence` attribute missing from `RouteResult`.

- [ ] **Step 3: Implement RouteConfidence and update RouteResult**

In `tcp/harness/router.py`, add the enum after the imports:

```python
from enum import Enum

class RouteConfidence(Enum):
    """How decisively the router resolved the task."""
    DETERMINISTIC = "deterministic"  # exactly 1 approved tool
    AMBIGUOUS = "ambiguous"          # 2+ approved tools
    NO_MATCH = "no_match"            # 0 approved tools
```

Update `RouteResult` to add the new fields:

```python
@dataclass(frozen=True)
class RouteResult:
    """Result of routing a task to a single tool."""

    selected_tool: ToolRecord | None
    confidence: RouteConfidence = RouteConfidence.NO_MATCH
    survivor_count: int = 0
    gate_result: GateResult | None = None
    bitmask_result: BitmaskFilterResult | None = None
    approved: tuple[ToolRecord, ...] = field(default_factory=tuple)
    approval_required: tuple[ToolRecord, ...] = field(default_factory=tuple)
    rejected: tuple[ToolRecord, ...] = field(default_factory=tuple)
    audit_log: tuple[AuditEntry, ...] = field(default_factory=tuple)
    # C3 extension points
    candidate_scores: dict[str, float] | None = None
    score_gap: float | None = None
```

At the end of `route_tool()`, before the return, compute confidence:

```python
    # --- Confidence classification ---
    total_survivors = len(approved) + len(approval_required)
    if total_survivors == 0:
        confidence = RouteConfidence.NO_MATCH
    elif total_survivors == 1:
        confidence = RouteConfidence.DETERMINISTIC
    else:
        confidence = RouteConfidence.AMBIGUOUS

    return RouteResult(
        selected_tool=selected,
        confidence=confidence,
        survivor_count=total_survivors,
        bitmask_result=bitmask_result,
        approved=tuple(approved),
        approval_required=tuple(approval_required),
        rejected=tuple(rejected + list(bitmask_result.rejected)),
        audit_log=tuple(audit),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_router.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `poetry run pytest tests/unit/ -v --tb=short`
Expected: All existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add tcp/harness/router.py tests/unit/test_router.py
git commit -m "feat(router): add RouteConfidence enum and survivor_count to RouteResult"
```

---

### Task 2: Add should_bypass_llm strategy function

**Files:**
- Create: `tcp/agent/routing_strategy.py`
- Test: `tests/unit/test_routing_strategy.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_routing_strategy.py`:

```python
"""Tests for the LLM bypass strategy."""

from __future__ import annotations

from tcp.agent.routing_strategy import should_bypass_llm
from tcp.harness.router import RouteConfidence, RouteResult


def _make_route_result(confidence: RouteConfidence, survivor_count: int = 1) -> RouteResult:
    return RouteResult(
        selected_tool=None,
        confidence=confidence,
        survivor_count=survivor_count,
    )


class TestShouldBypassLlm:
    """Default bypass strategy: bypass when DETERMINISTIC."""

    def test_bypass_on_deterministic(self):
        result = _make_route_result(RouteConfidence.DETERMINISTIC, survivor_count=1)
        assert should_bypass_llm(result) is True

    def test_no_bypass_on_ambiguous(self):
        result = _make_route_result(RouteConfidence.AMBIGUOUS, survivor_count=3)
        assert should_bypass_llm(result) is False

    def test_no_bypass_on_no_match(self):
        result = _make_route_result(RouteConfidence.NO_MATCH, survivor_count=0)
        assert should_bypass_llm(result) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_routing_strategy.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement routing_strategy.py**

Create `tcp/agent/routing_strategy.py`:

```python
"""Pluggable routing strategy for LLM bypass decisions.

The default strategy bypasses the LLM when the router resolves
deterministically (exactly 1 survivor).  C3 can swap in a
scoring-aware strategy without touching loop internals.
"""

from __future__ import annotations

from tcp.harness.router import RouteConfidence, RouteResult


def should_bypass_llm(result: RouteResult) -> bool:
    """Default strategy: bypass when exactly 1 survivor."""
    return result.confidence == RouteConfidence.DETERMINISTIC
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_routing_strategy.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tcp/agent/routing_strategy.py tests/unit/test_routing_strategy.py
git commit -m "feat(agent): add should_bypass_llm routing strategy with C3 seam"
```

---

### Task 3: Add bypass path to agent loop

**Files:**
- Modify: `tcp/agent/loop.py:44-63` (LoopMetrics), `tcp/agent/loop.py:66-204` (run_agent_loop)
- Test: `tests/unit/test_agent_loop.py` (add tests)

- [ ] **Step 1: Write failing tests for the bypass path**

Add to `tests/unit/test_agent_loop.py`:

```python
class TestBypassPath:
    """Deterministic bypass skips the LLM entirely."""

    async def test_bypass_invokes_executor_directly(self):
        """When bypass_tool is provided, no API call is made."""
        executor_calls = []

        def tracking_executor(tool_name: str, tool_input: dict) -> str:
            executor_calls.append(tool_name)
            return '{"status": "ok"}'

        metrics = await run_agent_loop(
            task_prompt="Do something",
            tools=[],
            mock_executor=tracking_executor,
            expected_tool="my-tool",
            task_name="bypass-test",
            bypass_tool="my-tool",
        )

        assert metrics.llm_bypassed is True
        assert metrics.tools_called == ("my-tool",)
        assert metrics.selected_tool_correct is True
        assert metrics.turns == 0
        assert metrics.input_tokens == 0
        assert executor_calls == ["my-tool"]

    async def test_bypass_wrong_tool_still_correct(self):
        """Bypass tool matches expected_tool — correctness is True."""
        metrics = await run_agent_loop(
            task_prompt="Do something",
            tools=[],
            mock_executor=_noop_executor,
            expected_tool="my-tool",
            task_name="bypass-match",
            bypass_tool="my-tool",
        )
        assert metrics.selected_tool_correct is True
        assert metrics.llm_bypassed is True

    async def test_no_bypass_when_not_specified(self):
        """Without bypass_tool, the normal LLM path runs."""
        mock_response = _make_response(
            content=[_make_text_block("ok")],
        )
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch(
            "tcp.agent.loop.anthropic.AsyncAnthropic", return_value=mock_client
        ):
            metrics = await run_agent_loop(
                task_prompt="Hello",
                tools=[{"name": "t", "description": "t", "input_schema": {"type": "object", "properties": {}}}],
                mock_executor=_noop_executor,
                expected_tool=None,
                task_name="no-bypass",
            )

        assert metrics.llm_bypassed is False
        mock_client.messages.create.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_agent_loop.py::TestBypassPath -v`
Expected: FAIL — `bypass_tool` not a valid parameter, `llm_bypassed` not on LoopMetrics.

- [ ] **Step 3: Implement bypass path**

In `tcp/agent/loop.py`, add `llm_bypassed` to `LoopMetrics`:

```python
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
    error_kind: str | None = None
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    tool_list_hash: str = ""
    llm_bypassed: bool = False
```

Add `bypass_tool` parameter to `run_agent_loop` and handle it at the top of the function:

```python
async def run_agent_loop(
    task_prompt: str,
    tools: list[dict],
    mock_executor: Callable[[str, dict], str],
    *,
    expected_tool: str | None,
    task_name: str,
    model: str = "claude-sonnet-4-6",
    max_turns: int = 5,
    bypass_tool: str | None = None,
) -> LoopMetrics:
    # --- Deterministic bypass path ---
    if bypass_tool is not None:
        total_start = time.perf_counter_ns()
        mock_executor(bypass_tool, {})
        total_end = time.perf_counter_ns()
        correct = bypass_tool == expected_tool
        return LoopMetrics(
            task_name=task_name,
            tool_count=len(tools),
            turns=0,
            first_token_latency_ms=0.0,
            total_response_time_ms=(total_end - total_start) / 1_000_000,
            input_tokens=0,
            output_tokens=0,
            tools_called=(bypass_tool,),
            selected_tool_correct=correct,
            error=None,
            llm_bypassed=True,
        )

    # ... rest of existing function unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_agent_loop.py -v`
Expected: All tests PASS including the 3 new bypass tests.

- [ ] **Step 5: Commit**

```bash
git add tcp/agent/loop.py tests/unit/test_agent_loop.py
git commit -m "feat(loop): deterministic bypass path — skip LLM when bypass_tool is set"
```

---

### Task 4: Add route_confidence and survivor_count to LoopMetrics and benchmark

**Files:**
- Modify: `tcp/agent/loop.py:44-63` (LoopMetrics — add route_confidence, survivor_count)
- Modify: `tcp/agent/benchmark.py:211-225` (_metrics_to_dict — serialize new fields)
- Test: `tests/unit/test_agent_loop.py` (add assertions)

- [ ] **Step 1: Write failing test for new LoopMetrics fields**

Add to `tests/unit/test_agent_loop.py` in `TestLoopMetrics`:

```python
    def test_new_routing_fields_default(self):
        m = LoopMetrics(
            task_name="t",
            tool_count=5,
            turns=1,
            first_token_latency_ms=10.0,
            total_response_time_ms=20.0,
            input_tokens=100,
            output_tokens=50,
            tools_called=(),
            selected_tool_correct=True,
            error=None,
        )
        assert m.route_confidence == ""
        assert m.survivor_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_agent_loop.py::TestLoopMetrics::test_new_routing_fields_default -v`
Expected: FAIL — `route_confidence` not on LoopMetrics.

- [ ] **Step 3: Add fields to LoopMetrics and _metrics_to_dict**

In `tcp/agent/loop.py`, add to `LoopMetrics` after `llm_bypassed`:

```python
    route_confidence: str = ""
    survivor_count: int = 0
```

In `tcp/agent/benchmark.py`, update `_metrics_to_dict` to include the new fields:

```python
def _metrics_to_dict(m: LoopMetrics) -> dict:
    """Serialize LoopMetrics to a JSON-safe dict."""
    return {
        "task_name": m.task_name,
        "tool_count": m.tool_count,
        "turns": m.turns,
        "first_token_latency_ms": m.first_token_latency_ms,
        "total_response_time_ms": m.total_response_time_ms,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "tools_called": list(m.tools_called),
        "selected_tool_correct": m.selected_tool_correct,
        "error": m.error,
        "error_kind": m.error_kind,
        "llm_bypassed": m.llm_bypassed,
        "route_confidence": m.route_confidence,
        "survivor_count": m.survivor_count,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_agent_loop.py tests/unit/test_agent_benchmark.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add tcp/agent/loop.py tcp/agent/benchmark.py tests/unit/test_agent_loop.py
git commit -m "feat(loop): add route_confidence and survivor_count to LoopMetrics"
```

---

### Task 5: Build ambiguous task corpus

**Files:**
- Create: `tcp/agent/ambiguous_tasks.py`
- Modify: `tcp/agent/mock_executors.py:12-118` (add mock responses for new tools)
- Test: `tests/unit/test_ambiguous_tasks.py` (new)

- [ ] **Step 1: Write failing tests for ambiguous tasks**

Create `tests/unit/test_ambiguous_tasks.py`:

```python
"""Tests for the ambiguous task corpus."""

from __future__ import annotations

import pytest

from tcp.agent.ambiguous_tasks import build_ambiguous_tasks, AmbiguousTask


class TestAmbiguousTaskStructure:
    """Each ambiguous task has required fields."""

    def test_returns_list(self):
        tasks = build_ambiguous_tasks()
        assert isinstance(tasks, list)
        assert len(tasks) >= 6

    def test_all_have_required_fields(self):
        for task in build_ambiguous_tasks():
            assert isinstance(task, AmbiguousTask)
            assert task.agent_task.name
            assert task.agent_task.prompt
            assert task.agent_task.expected_tool is not None
            assert task.selection_request is not None
            assert task.ambiguity_reason

    def test_selection_requests_have_no_required_commands(self):
        """Ambiguous tasks use capability flags/formats, NOT specific commands."""
        for task in build_ambiguous_tasks():
            assert len(task.selection_request.required_commands) == 0, (
                f"Task {task.agent_task.name!r} has required_commands — "
                f"ambiguous tasks must use broader filters"
            )

    def test_synthetic_tools_provided(self):
        """Each task provides its synthetic tool records."""
        for task in build_ambiguous_tasks():
            assert len(task.synthetic_tools) >= 2, (
                f"Task {task.agent_task.name!r} needs 2+ synthetic tools, "
                f"got {len(task.synthetic_tools)}"
            )

    def test_expected_tool_in_synthetic_tools(self):
        """The expected tool appears in the synthetic tool set."""
        for task in build_ambiguous_tasks():
            tool_names = {t.tool_name for t in task.synthetic_tools}
            assert task.agent_task.expected_tool in tool_names, (
                f"Task {task.agent_task.name!r}: expected tool "
                f"{task.agent_task.expected_tool!r} not in synthetic tools "
                f"{sorted(tool_names)}"
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_ambiguous_tasks.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement ambiguous_tasks.py**

Create `tcp/agent/ambiguous_tasks.py`:

```python
"""Ambiguous task corpus for the layered deterministic router.

Each task is designed so TCP filtering admits 2-5 tools but the
correct answer depends on prompt context only the LLM can interpret.
Selection requests use capability flags and formats (NOT required_commands)
so the filter admits multiple tools.
"""

from __future__ import annotations

from dataclasses import dataclass

from tcp.core.descriptors import CapabilityFlags
from tcp.agent.tasks import AgentTask
from tcp.harness.models import ToolRecord, ToolSelectionRequest


@dataclass(frozen=True)
class AmbiguousTask:
    """An ambiguous task with multiple viable tools."""

    agent_task: AgentTask
    selection_request: ToolSelectionRequest
    ambiguity_reason: str
    synthetic_tools: tuple[ToolRecord, ...]


def _tool(
    name: str,
    *,
    commands: frozenset[str] = frozenset(),
    capability_flags: int = 0,
    input_formats: frozenset[str] = frozenset(),
    output_formats: frozenset[str] = frozenset(),
) -> ToolRecord:
    """Helper to build a synthetic ToolRecord."""
    return ToolRecord(
        tool_name=name,
        descriptor_source="synthetic-ambiguous",
        descriptor_version="1.0",
        capability_flags=capability_flags,
        risk_level="safe",
        commands=commands,
        input_formats=input_formats,
        output_formats=output_formats,
    )


def build_ambiguous_tasks() -> list[AmbiguousTask]:
    """Build 6 ambiguous tasks where filtering yields 2-5 survivors."""

    file_flags = int(CapabilityFlags.FILE_READ)
    text_flags = int(CapabilityFlags.TEXT_PROCESSING)
    net_flags = int(CapabilityFlags.NETWORK_ACCESS)

    return [
        # --- 1. Pattern search: grep vs ripgrep vs fs-search-files ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="pattern search in code",
                prompt=(
                    "Find all lines containing 'TODO' across the codebase. "
                    "I need line numbers and file paths."
                ),
                expected_tool="grep",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=file_flags | text_flags,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "grep, ripgrep, and fs-search-files all have FILE_READ | TEXT_PROCESSING "
                "flags. grep is correct because the prompt asks for line-level pattern "
                "matching with line numbers."
            ),
            synthetic_tools=(
                _tool("grep", capability_flags=file_flags | text_flags,
                      commands=frozenset({"grep", "search"}),
                      output_formats=frozenset({"text"})),
                _tool("ripgrep", capability_flags=file_flags | text_flags,
                      commands=frozenset({"rg", "search"}),
                      output_formats=frozenset({"text"})),
                _tool("fs-search-files", capability_flags=file_flags | text_flags,
                      commands=frozenset({"search_files"}),
                      output_formats=frozenset({"json"})),
            ),
        ),
        # --- 2. Fetch remote data: curl vs http-fetch vs wget ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="fetch structured API data",
                prompt=(
                    "Get the JSON response from our deploy status API endpoint "
                    "and parse the 'status' field."
                ),
                expected_tool="http-fetch",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=net_flags,
                required_output_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "curl, http-fetch, and wget all have NETWORK_ACCESS and can "
                "output JSON. http-fetch is correct because the prompt asks for "
                "structured JSON parsing, which http-fetch handles natively."
            ),
            synthetic_tools=(
                _tool("curl", capability_flags=net_flags,
                      commands=frozenset({"curl", "fetch"}),
                      output_formats=frozenset({"text", "json"})),
                _tool("http-fetch", capability_flags=net_flags,
                      commands=frozenset({"fetch", "http_get"}),
                      output_formats=frozenset({"json"})),
                _tool("wget", capability_flags=net_flags,
                      commands=frozenset({"wget", "download"}),
                      output_formats=frozenset({"text", "json"})),
            ),
        ),
        # --- 3. Transform JSON: jq vs python-exec vs node-exec ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="transform JSON with sorted keys",
                prompt=(
                    "Take the JSON file at /tmp/config.json, sort all keys "
                    "alphabetically, and pretty-print the result."
                ),
                expected_tool="jq",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=text_flags,
                required_input_formats={"json"},
                required_output_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "jq, python-exec, and node-exec all handle JSON input/output "
                "with TEXT_PROCESSING. jq is correct because it's purpose-built "
                "for JSON transformation with native sort-keys support."
            ),
            synthetic_tools=(
                _tool("jq", capability_flags=text_flags | file_flags,
                      commands=frozenset({"jq", "transform"}),
                      input_formats=frozenset({"json"}),
                      output_formats=frozenset({"json"})),
                _tool("python-exec", capability_flags=text_flags | file_flags,
                      commands=frozenset({"python", "exec"}),
                      input_formats=frozenset({"json", "text"}),
                      output_formats=frozenset({"json", "text"})),
                _tool("node-exec", capability_flags=text_flags | file_flags,
                      commands=frozenset({"node", "exec"}),
                      input_formats=frozenset({"json", "text"}),
                      output_formats=frozenset({"json", "text"})),
            ),
        ),
        # --- 4. Write config file: fs-write-file vs tee vs editor ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="write new config file",
                prompt=(
                    "Create a new configuration file at /etc/app/settings.yaml "
                    "with the database connection string. The file doesn't exist yet."
                ),
                expected_tool="fs-write-file",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=int(CapabilityFlags.FILE_WRITE),
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "fs-write-file, tee, and editor all have FILE_WRITE. "
                "fs-write-file is correct because the prompt asks to create "
                "a new file (not append or edit), and it's the safest atomic write."
            ),
            synthetic_tools=(
                _tool("fs-write-file",
                      capability_flags=int(CapabilityFlags.FILE_WRITE),
                      commands=frozenset({"write_file", "create_file"})),
                _tool("tee",
                      capability_flags=int(CapabilityFlags.FILE_WRITE),
                      commands=frozenset({"tee", "write"})),
                _tool("editor",
                      capability_flags=int(CapabilityFlags.FILE_WRITE) | int(CapabilityFlags.FILE_READ),
                      commands=frozenset({"edit", "open"})),
            ),
        ),
        # --- 5. Inspect service: ps vs systemctl vs pgrep ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="check service status",
                prompt=(
                    "Check whether the postgresql service is running and show "
                    "its current status. I need to know if it's active."
                ),
                expected_tool="systemctl",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=int(CapabilityFlags.SYSTEM_INFO),
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "ps, systemctl, and pgrep all have SYSTEM_INFO. systemctl is "
                "correct because the prompt asks about a service by name and "
                "wants active/inactive status — systemctl is canonical for "
                "service management."
            ),
            synthetic_tools=(
                _tool("systemctl",
                      capability_flags=int(CapabilityFlags.SYSTEM_INFO) | int(CapabilityFlags.AUTH_REQUIRED),
                      commands=frozenset({"systemctl", "service"})),
                _tool("ps",
                      capability_flags=int(CapabilityFlags.SYSTEM_INFO),
                      commands=frozenset({"ps", "process_list"})),
                _tool("pgrep",
                      capability_flags=int(CapabilityFlags.SYSTEM_INFO),
                      commands=frozenset({"pgrep", "process_find"})),
            ),
        ),
        # --- 6. Diff files: diff vs git-diff vs colordiff ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="diff two config versions",
                prompt=(
                    "Show me the differences between /tmp/config-v1.yaml and "
                    "/tmp/config-v2.yaml. These are standalone files, not in "
                    "a git repository."
                ),
                expected_tool="diff",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=file_flags | text_flags,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "diff, git-diff, and colordiff all have FILE_READ | TEXT_PROCESSING. "
                "diff is correct because the prompt explicitly says 'not in a git "
                "repository' — git-diff would fail or give wrong results."
            ),
            synthetic_tools=(
                _tool("diff", capability_flags=file_flags | text_flags,
                      commands=frozenset({"diff", "compare"}),
                      output_formats=frozenset({"text"})),
                _tool("git-diff", capability_flags=file_flags | text_flags,
                      commands=frozenset({"git_diff", "diff"}),
                      output_formats=frozenset({"text"})),
                _tool("colordiff", capability_flags=file_flags | text_flags,
                      commands=frozenset({"colordiff", "diff"}),
                      output_formats=frozenset({"text"})),
            ),
        ),
    ]
```

- [ ] **Step 4: Add mock responses for new synthetic tools**

In `tcp/agent/mock_executors.py`, add to `MOCK_RESPONSES`:

```python
    # --- Synthetic ambiguous corpus ---
    "ripgrep": '{"matches": ["src/main.py:15:TODO fix auth"]}',
    "http-fetch": '{"status": 200, "body": {"deploy_status": "healthy"}}',
    "wget": '{"status": 200, "saved": "/tmp/output"}',
    "python-exec": '{"output": "result"}',
    "node-exec": '{"output": "result"}',
    "tee": '{"status": "written", "bytes": 128}',
    "editor": '{"status": "opened", "path": "/tmp/file"}',
    "ps": '{"processes": [{"pid": 1234, "name": "postgres"}]}',
    "pgrep": '{"pids": [1234, 1235]}',
    "diff": '{"diff": "--- v1\\n+++ v2\\n@@ -1 +1 @@\\n-old\\n+new"}',
    "colordiff": '{"diff": "--- v1\\n+++ v2\\n@@ -1 +1 @@\\n-old\\n+new"}',
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_ambiguous_tasks.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tcp/agent/ambiguous_tasks.py tcp/agent/mock_executors.py tests/unit/test_ambiguous_tasks.py
git commit -m "feat(agent): ambiguous task corpus — 6 tasks with 2-5 viable tools each"
```

---

### Task 6: Validate survivor counts for ambiguous tasks

**Files:**
- Test: `tests/unit/test_ambiguous_tasks.py` (add validation test)

This task verifies the spec's validation gate: each ambiguous task must produce 2-5 survivors when filtered against its own synthetic tools.

- [ ] **Step 1: Write the validation test**

Add to `tests/unit/test_ambiguous_tasks.py`:

```python
from tcp.harness.gating import RuntimeEnvironment, gate_tools


class TestAmbiguousSurvivorCounts:
    """Each ambiguous task produces 2-5 survivors from its synthetic tools."""

    def test_survivor_counts_in_range(self):
        env = RuntimeEnvironment(
            network_enabled=True,  # allow network tools for fetch tasks
            file_access_enabled=True,
            stdin_enabled=True,
            installed_tools=frozenset(),
        )
        for task in build_ambiguous_tasks():
            tools = list(task.synthetic_tools)
            result = gate_tools(tools, task.selection_request, env)
            survivors = len(result.approved_tools) + len(result.approval_required_tools)
            assert 2 <= survivors <= 5, (
                f"Task {task.agent_task.name!r}: expected 2-5 survivors, "
                f"got {survivors} (approved={len(result.approved_tools)}, "
                f"approval_required={len(result.approval_required_tools)}, "
                f"rejected={len(result.rejected_tools)})"
            )

    def test_expected_tool_survives_filtering(self):
        env = RuntimeEnvironment(
            network_enabled=True,
            file_access_enabled=True,
            stdin_enabled=True,
            installed_tools=frozenset(),
        )
        for task in build_ambiguous_tasks():
            tools = list(task.synthetic_tools)
            result = gate_tools(tools, task.selection_request, env)
            survivor_names = {t.tool_name for t in result.approved_tools}
            survivor_names |= {t.tool_name for t in result.approval_required_tools}
            assert task.agent_task.expected_tool in survivor_names, (
                f"Task {task.agent_task.name!r}: expected tool "
                f"{task.agent_task.expected_tool!r} was filtered out. "
                f"Survivors: {sorted(survivor_names)}"
            )
```

- [ ] **Step 2: Run the validation**

Run: `poetry run pytest tests/unit/test_ambiguous_tasks.py::TestAmbiguousSurvivorCounts -v`
Expected: PASS — all tasks produce 2-5 survivors with expected tool present. If any fail, adjust the capability flags in the task definitions until they pass (this is the validation gate from the spec).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_ambiguous_tasks.py
git commit -m "test: validate ambiguous task survivor counts (2-5 per task)"
```

---

### Task 7: Three-lane benchmark reporting

**Files:**
- Create: `tcp/agent/lane_report.py`
- Test: `tests/unit/test_lane_report.py` (new)

- [ ] **Step 1: Write failing tests for lane reporting**

Create `tests/unit/test_lane_report.py`:

```python
"""Tests for three-lane benchmark reporting."""

from __future__ import annotations

import pytest

from tcp.agent.lane_report import LaneReport, build_lane_report
from tcp.agent.loop import LoopMetrics


def _make_metrics(
    task_name: str = "test",
    correct: bool = True,
    input_tokens: int = 500,
    bypassed: bool = False,
    confidence: str = "deterministic",
    survivor_count: int = 1,
) -> LoopMetrics:
    return LoopMetrics(
        task_name=task_name,
        tool_count=10,
        turns=0 if bypassed else 2,
        first_token_latency_ms=0.0 if bypassed else 100.0,
        total_response_time_ms=1.0 if bypassed else 200.0,
        input_tokens=0 if bypassed else input_tokens,
        output_tokens=0 if bypassed else 50,
        tools_called=("tool-a",),
        selected_tool_correct=correct,
        error=None,
        llm_bypassed=bypassed,
        route_confidence=confidence,
        survivor_count=survivor_count,
    )


class TestBuildLaneReport:
    """Lane report splits metrics by confidence."""

    def test_deterministic_lane(self):
        metrics = [
            _make_metrics(confidence="deterministic", bypassed=True, correct=True),
            _make_metrics(confidence="deterministic", bypassed=True, correct=True),
        ]
        report = build_lane_report(metrics)
        assert report.deterministic_count == 2
        assert report.deterministic_correct_rate == pytest.approx(1.0)

    def test_ambiguous_lane(self):
        metrics = [
            _make_metrics(confidence="ambiguous", bypassed=False, correct=True, survivor_count=3),
            _make_metrics(confidence="ambiguous", bypassed=False, correct=False, survivor_count=3),
        ]
        report = build_lane_report(metrics)
        assert report.ambiguous_count == 2
        assert report.ambiguous_correct_rate == pytest.approx(0.5)

    def test_no_match_lane(self):
        metrics = [
            _make_metrics(confidence="no_match", bypassed=False, correct=True, survivor_count=0),
        ]
        report = build_lane_report(metrics)
        assert report.no_match_count == 1

    def test_bypass_ratio(self):
        metrics = [
            _make_metrics(confidence="deterministic", bypassed=True),
            _make_metrics(confidence="deterministic", bypassed=True),
            _make_metrics(confidence="ambiguous", bypassed=False, survivor_count=3),
        ]
        report = build_lane_report(metrics)
        assert report.bypass_ratio == pytest.approx(2 / 3)

    def test_ambiguous_llm_lift(self):
        """LLM lift = ambiguous LLM correctness - _select_best baseline.

        When _select_best baseline isn't provided, lift is just the
        ambiguous correct rate.
        """
        metrics = [
            _make_metrics(confidence="ambiguous", bypassed=False, correct=True, survivor_count=3),
            _make_metrics(confidence="ambiguous", bypassed=False, correct=True, survivor_count=3),
        ]
        report = build_lane_report(metrics, select_best_correct_rate=0.5)
        assert report.ambiguous_llm_lift == pytest.approx(0.5)

    def test_empty_metrics(self):
        report = build_lane_report([])
        assert report.deterministic_count == 0
        assert report.ambiguous_count == 0
        assert report.bypass_ratio == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/test_lane_report.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement lane_report.py**

Create `tcp/agent/lane_report.py`:

```python
"""Three-lane benchmark reporting for the layered deterministic router.

Splits trial metrics into deterministic / ambiguous / no-match lanes
and computes per-lane statistics.
"""

from __future__ import annotations

from dataclasses import dataclass

from tcp.agent.loop import LoopMetrics


@dataclass(frozen=True)
class LaneReport:
    """Per-lane summary statistics."""

    deterministic_count: int
    deterministic_correct_rate: float
    deterministic_mean_latency_ms: float

    ambiguous_count: int
    ambiguous_correct_rate: float
    ambiguous_mean_latency_ms: float
    ambiguous_mean_tokens: float

    no_match_count: int
    no_match_correct_rate: float

    bypass_ratio: float
    ambiguous_llm_lift: float

    def summary_table(self) -> str:
        lines = [
            f"{'Lane':<20} {'Count':>6} {'Correct':>8} {'Latency':>10} {'Tokens':>8}",
            "-" * 55,
            f"{'Deterministic':<20} {self.deterministic_count:>6} "
            f"{self.deterministic_correct_rate:>7.0%} "
            f"{self.deterministic_mean_latency_ms:>9.0f}ms {'—':>8}",
            f"{'Ambiguous':<20} {self.ambiguous_count:>6} "
            f"{self.ambiguous_correct_rate:>7.0%} "
            f"{self.ambiguous_mean_latency_ms:>9.0f}ms "
            f"{self.ambiguous_mean_tokens:>8.0f}",
            f"{'No-match':<20} {self.no_match_count:>6} "
            f"{self.no_match_correct_rate:>7.0%} {'—':>10} {'—':>8}",
            "-" * 55,
            f"Bypass ratio: {self.bypass_ratio:.0%}",
            f"Ambiguous LLM lift: {self.ambiguous_llm_lift:+.0%}",
        ]
        return "\n".join(lines)


def build_lane_report(
    metrics: list[LoopMetrics],
    *,
    select_best_correct_rate: float = 0.0,
) -> LaneReport:
    """Build a three-lane report from a flat list of LoopMetrics."""
    det = [m for m in metrics if m.route_confidence == "deterministic"]
    amb = [m for m in metrics if m.route_confidence == "ambiguous"]
    nm = [m for m in metrics if m.route_confidence == "no_match"]

    det_n = len(det)
    amb_n = len(amb)
    nm_n = len(nm)
    total = len(metrics)

    det_correct = sum(1 for m in det if m.selected_tool_correct) / det_n if det_n else 0.0
    amb_correct = sum(1 for m in amb if m.selected_tool_correct) / amb_n if amb_n else 0.0
    nm_correct = sum(1 for m in nm if m.selected_tool_correct) / nm_n if nm_n else 0.0

    det_latency = sum(m.total_response_time_ms for m in det) / det_n if det_n else 0.0
    amb_latency = sum(m.total_response_time_ms for m in amb) / amb_n if amb_n else 0.0
    amb_tokens = sum(m.input_tokens for m in amb) / amb_n if amb_n else 0.0

    bypass = sum(1 for m in metrics if m.llm_bypassed) / total if total else 0.0
    lift = amb_correct - select_best_correct_rate

    return LaneReport(
        deterministic_count=det_n,
        deterministic_correct_rate=det_correct,
        deterministic_mean_latency_ms=det_latency,
        ambiguous_count=amb_n,
        ambiguous_correct_rate=amb_correct,
        ambiguous_mean_latency_ms=amb_latency,
        ambiguous_mean_tokens=amb_tokens,
        no_match_count=nm_n,
        no_match_correct_rate=nm_correct,
        bypass_ratio=bypass,
        ambiguous_llm_lift=lift,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_lane_report.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tcp/agent/lane_report.py tests/unit/test_lane_report.py
git commit -m "feat(agent): three-lane benchmark reporting (deterministic/ambiguous/no-match)"
```

---

### Task 8: Wire bypass + lane reporting into the benchmark

**Files:**
- Modify: `tcp/agent/benchmark.py` (add `run_layered_benchmark`)
- Modify: `tcp/agent/cli.py` (add `--layered` CLI mode)
- Test: `tests/unit/test_agent_benchmark.py` (add layered benchmark test)

- [ ] **Step 1: Write failing test for run_layered_benchmark**

Add to `tests/unit/test_agent_benchmark.py`:

```python
from tcp.agent.benchmark import run_layered_benchmark
from tcp.agent.lane_report import LaneReport


@pytest.mark.asyncio
class TestRunLayeredBenchmark:
    """Layered benchmark runs deterministic and ambiguous tasks."""

    async def test_returns_lane_report(self):
        call_count = 0

        async def mock_loop(task_prompt, tools, mock_executor, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_metrics(
                task_name=kwargs.get("task_name", "test"),
                tool_count=len(tools),
            )

        with patch("tcp.agent.benchmark.run_agent_loop", side_effect=mock_loop):
            report = await run_layered_benchmark(repetitions=1)

        assert isinstance(report, LaneReport)
        # Should have deterministic + ambiguous + no-match tasks
        assert report.deterministic_count + report.ambiguous_count + report.no_match_count > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_agent_benchmark.py::TestRunLayeredBenchmark -v`
Expected: FAIL — `run_layered_benchmark` not found.

- [ ] **Step 3: Implement run_layered_benchmark**

Add to `tcp/agent/benchmark.py`:

```python
from tcp.agent.ambiguous_tasks import build_ambiguous_tasks
from tcp.agent.lane_report import LaneReport, build_lane_report
from tcp.agent.routing_strategy import should_bypass_llm
from tcp.harness.router import RouteConfidence, RouteResult, route_tool


async def run_layered_benchmark(
    *,
    repetitions: int = 3,
    model: str = "claude-sonnet-4-6",
    results_path: Path | None = None,
) -> LaneReport:
    """Run the layered benchmark: deterministic bypass + ambiguous LLM path.

    1. Builds combined task set (12 deterministic + 6 ambiguous + 3 no-match)
    2. For each task, runs per-task filtering and classifies confidence
    3. DETERMINISTIC: bypass LLM, invoke executor directly
    4. AMBIGUOUS/NO_MATCH: send filtered tools to LLM
    5. Returns three-lane report
    """
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.gating import RuntimeEnvironment, gate_tools
    from tcp.harness.normalize import normalize_capability_descriptor
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    # Build tasks
    det_tasks = build_agent_tasks()
    amb_tasks_raw = build_ambiguous_tasks()
    amb_tasks = [
        AgentTask(
            name=at.agent_task.name,
            prompt=at.agent_task.prompt,
            expected_tool=at.agent_task.expected_tool,
            selection_request=at.selection_request,
        )
        for at in amb_tasks_raw
    ]
    all_tasks = det_tasks + amb_tasks

    # Build corpus (include synthetic tools from ambiguous tasks)
    entries = build_mcp_corpus()
    corpus_schemas = corpus_to_anthropic_schemas(entries)
    records = [normalize_capability_descriptor(e.descriptor) for e in entries]

    # Add synthetic tool records and schemas
    for at in amb_tasks_raw:
        for tool in at.synthetic_tools:
            records.append(tool)
            corpus_schemas.append({
                "name": tool.tool_name,
                "description": f"Synthetic tool: {tool.tool_name}",
                "input_schema": {"type": "object", "properties": {}},
            })

    schema_by_name = {s["name"]: s for s in corpus_schemas}
    all_names = frozenset(r.tool_name for r in records)
    env = RuntimeEnvironment(
        network_enabled=False,
        file_access_enabled=True,
        stdin_enabled=True,
        installed_tools=all_names,
    )
    default_request = ToolSelectionRequest.from_kwargs(
        preferred_criteria="speed",
        require_auto_approval=False,
    )

    mock_exec = get_mock_executor()
    all_metrics: list[LoopMetrics] = []

    for task in all_tasks:
        request = task.selection_request or default_request
        gate_result = gate_tools(records, request, env)
        survivor_names = {t.tool_name for t in gate_result.approved_tools}
        survivor_names |= {t.tool_name for t in gate_result.approval_required_tools}
        filtered_schemas = [schema_by_name[n] for n in survivor_names if n in schema_by_name]

        survivor_count = len(survivor_names)
        if survivor_count == 0:
            confidence = RouteConfidence.NO_MATCH
        elif survivor_count == 1:
            confidence = RouteConfidence.DETERMINISTIC
        else:
            confidence = RouteConfidence.AMBIGUOUS

        for _rep in range(repetitions):
            # Use the pluggable strategy (C3 seam)
            route_result = RouteResult(
                selected_tool=None,
                confidence=confidence,
                survivor_count=survivor_count,
            )

            if should_bypass_llm(route_result):
                bypass_name = next(iter(survivor_names))
                metrics = await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=filtered_schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                    bypass_tool=bypass_name,
                )
            else:
                metrics = await run_agent_loop(
                    task_prompt=task.prompt,
                    tools=filtered_schemas,
                    mock_executor=mock_exec,
                    expected_tool=task.expected_tool,
                    task_name=task.name,
                    model=model,
                )

            # Patch routing metadata onto metrics
            metrics = LoopMetrics(
                task_name=metrics.task_name,
                tool_count=metrics.tool_count,
                turns=metrics.turns,
                first_token_latency_ms=metrics.first_token_latency_ms,
                total_response_time_ms=metrics.total_response_time_ms,
                input_tokens=metrics.input_tokens,
                output_tokens=metrics.output_tokens,
                tools_called=metrics.tools_called,
                selected_tool_correct=metrics.selected_tool_correct,
                error=metrics.error,
                error_kind=metrics.error_kind,
                cache_read_tokens=metrics.cache_read_tokens,
                cache_creation_tokens=metrics.cache_creation_tokens,
                tool_list_hash=metrics.tool_list_hash,
                llm_bypassed=metrics.llm_bypassed,
                route_confidence=confidence.value,
                survivor_count=survivor_count,
            )
            all_metrics.append(metrics)

    return build_lane_report(all_metrics)
```

- [ ] **Step 4: Add --layered CLI mode**

In `tcp/agent/cli.py`, add to the mutually exclusive group:

```python
    mode.add_argument(
        "--layered",
        action="store_true",
        help="Run layered benchmark (deterministic bypass + ambiguous LLM)",
    )
```

Add the elif branch after `--scale`:

```python
    elif args.layered:
        if not _cmd_preflight():
            sys.exit(1)
        asyncio.run(_cmd_layered(args.reps, args.model, args.output))
```

Add the handler function:

```python
async def _cmd_layered(reps: int, model: str, output: Path | None) -> None:
    """Run the layered benchmark."""
    from tcp.agent.benchmark import run_layered_benchmark

    print(
        f"\n--- Layered benchmark ---"
        f"\n  Reps: {reps}"
        f"\n  Model: {model}"
    )
    if output:
        print(f"  Results: {output}")

    report = await run_layered_benchmark(
        repetitions=reps,
        model=model,
        results_path=output,
    )

    print(f"\n{report.summary_table()}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_agent_benchmark.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Run full test suite**

Run: `poetry run pytest tests/unit/ -v --tb=short`
Expected: All tests PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add tcp/agent/benchmark.py tcp/agent/cli.py tests/unit/test_agent_benchmark.py
git commit -m "feat(agent): layered benchmark — deterministic bypass + ambiguous LLM + three-lane report"
```

---

### Task 9: End-to-end preflight validation

**Files:**
- Test: `tests/unit/test_ambiguous_tasks.py` (add end-to-end filtering test)

This ensures the full pipeline (ambiguous tasks + corpus + filtering + confidence classification) works together before running the real benchmark.

- [ ] **Step 1: Write end-to-end filtering test**

Add to `tests/unit/test_ambiguous_tasks.py`:

```python
from tcp.harness.router import RouteConfidence, route_tool


class TestEndToEndClassification:
    """Full pipeline: ambiguous tasks get AMBIGUOUS confidence."""

    def test_ambiguous_tasks_classified_ambiguous(self):
        env = RuntimeEnvironment(
            network_enabled=True,
            file_access_enabled=True,
            stdin_enabled=True,
            installed_tools=frozenset(),
        )
        for task in build_ambiguous_tasks():
            result = route_tool(
                list(task.synthetic_tools),
                task.selection_request,
                env,
            )
            assert result.confidence == RouteConfidence.AMBIGUOUS, (
                f"Task {task.agent_task.name!r}: expected AMBIGUOUS, "
                f"got {result.confidence} with {result.survivor_count} survivors"
            )
            assert result.survivor_count >= 2
```

- [ ] **Step 2: Run the test**

Run: `poetry run pytest tests/unit/test_ambiguous_tasks.py::TestEndToEndClassification -v`
Expected: PASS — all ambiguous tasks get AMBIGUOUS confidence.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_ambiguous_tasks.py
git commit -m "test: end-to-end validation — ambiguous tasks classified as AMBIGUOUS"
```

---

### Task 10: Final integration — run full test suite and verify CLI

**Files:** None new — this is a validation task.

- [ ] **Step 1: Run full test suite**

Run: `poetry run pytest tests/unit/ -v --tb=short`
Expected: All tests PASS with no regressions.

- [ ] **Step 2: Verify CLI help**

Run: `poetry run python -m tcp.agent --help`
Expected: Output includes `--layered` option.

- [ ] **Step 3: Run preflight**

Run: `poetry run python -m tcp.agent --preflight`
Expected: All checks pass.

- [ ] **Step 4: Commit any fixes**

If any fixes were needed, commit them:

```bash
git add -u
git commit -m "fix: address integration issues from layered router implementation"
```
