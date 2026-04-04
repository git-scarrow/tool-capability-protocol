# Layered Deterministic Router — Design Spec

**Date:** 2026-04-04
**Status:** Approved
**Approach:** C2 (split router + ambiguous task corpus), C3 interface seams reserved

## Motivation

TCP's per-task filtering narrows tool sets so aggressively that at scale (650 tools,
MT-7) the LLM achieves 100% correctness — but the filter is doing all the work. When
filtering yields exactly 1 tool, the LLM is rubber-stamping a decision TCP already
made. This design formalizes that observation: route deterministically when the filter
is decisive, involve the LLM only when genuine ambiguity remains.

**Current working model:** The 12 existing benchmark tasks use narrow
`required_commands` filters that typically resolve to 1 tool. This fits the evidence
from MT-4 through MT-7, but the actual survivor counts at scale haven't been
independently verified from logs.

**Key assumption:** The 1-tool-per-task pattern at scale reflects narrow task
definitions, not inherent filter precision. Broader selection requests (capability
flags, formats) should produce genuine multi-tool ambiguity.

**Most decision-relevant unknown:** What fraction of real-world agent tasks produce
2+ survivors? If most do, the deterministic bypass is a niche optimization. If few do,
the LLM is unnecessary overhead for tool selection.

## Section 1: Router Split — RouteConfidence

### New enum on RouteResult

```python
class RouteConfidence(Enum):
    DETERMINISTIC = "deterministic"  # exactly 1 approved tool
    AMBIGUOUS = "ambiguous"          # 2+ approved tools
    NO_MATCH = "no_match"            # 0 approved tools
```

`route_tool()` sets this based on `len(approved)`. New fields on `RouteResult`:

- `confidence: RouteConfidence`
- `survivor_count: int`
- `candidate_scores: dict[str, float] | None = None` — C3 extension point
- `score_gap: float | None = None` — C3 extension point

### Agent loop behavior by confidence

| Confidence    | Action                                    |
|---------------|-------------------------------------------|
| DETERMINISTIC | Skip LLM, invoke tool directly, record `llm_bypassed=True` |
| AMBIGUOUS     | Send filtered set to LLM as today         |
| NO_MATCH      | Send to LLM with empty tools (explain why it can't help) |

### C3 hook

`RouteConfidence` can later carry a float score or `score_gap`. The loop's branch
point changes from `== DETERMINISTIC` to `score_gap >= threshold`. The interface
supports this without structural rework.

## Section 2: Ambiguous Task Corpus

### Design principles

Each ambiguous task specifies:
- `selection_request` — broad enough to admit 2+ tools (capability flags and formats,
  NOT specific command names)
- `expected_tool` — one correct answer, chosen by prompt intent not filter
- `ambiguity_reason` — documents why multiple tools survive (for analysis)

### Target ambiguous tasks (6-8)

| Task | Prompt | Expected survivors | Right answer | Why ambiguous |
|------|--------|--------------------|--------------|---------------|
| Pattern search in code | "Find all TODO comments in the codebase" | grep, fs-search-files, ripgrep | grep | All handle text search; grep is best for pattern matching |
| Fetch remote data | "Get the latest deploy status from our API" | curl, http-fetch, wget | http-fetch | All do HTTP; fetch is the structured tool |
| Transform JSON | "Reformat this JSON with sorted keys" | jq, python-exec, sed | jq | Multiple tools process text; jq is purpose-built |
| Write to file | "Save this config to /etc/app.conf" | fs-write-file, tee, editor | fs-write-file | All can write; fs-write is safest |
| Inspect process | "Check if nginx is running" | ps, systemctl, pgrep | systemctl | All can check processes; systemctl is canonical for services |
| Diff two files | "Show me what changed between v1 and v2 of the config" | diff, git-diff, colordiff | diff | All produce diffs; plain diff is correct for non-git files |

These need corresponding synthetic tool records in the corpus. Selection requests use
capability flags and format requirements so the filter admits multiple tools but
doesn't decide between them.

### Validation gate

Before running the full benchmark, verify survivor counts per ambiguous task. If a
task produces only 1 survivor, the task definition is too narrow — fix it before
proceeding. If a task produces 10+ survivors, the definition is too broad.

Target: 2-5 survivors per ambiguous task.

## Section 3: Benchmark Metrics and Reporting

### Three-lane reporting

```
┌─────────────────┬──────────┬────────────┬─────────────────────┐
│ Lane            │ Tasks    │ Measures   │ Key metric          │
├─────────────────┼──────────┼────────────┼─────────────────────┤
│ Deterministic   │ ~12      │ TCP alone  │ Bypass accuracy,    │
│ (1 survivor)    │          │            │ latency = 0 LLM     │
├─────────────────┼──────────┼────────────┼─────────────────────┤
│ Ambiguous       │ ~6-8     │ TCP + LLM  │ LLM accuracy,       │
│ (2+ survivors)  │          │            │ token cost          │
├─────────────────┼──────────┼────────────┼─────────────────────┤
│ No-match        │ ~3       │ TCP rejects│ True negative rate  │
│ (0 survivors)   │          │            │                     │
└─────────────────┴──────────┴────────────┴─────────────────────┘
```

### New fields on LoopMetrics

- `route_confidence: str` — which lane this trial fell into
- `llm_bypassed: bool` — whether the LLM was skipped
- `survivor_count: int` — how many tools passed filtering

### Summary metrics

- Per-lane correctness rate, mean latency, mean token cost
- `bypass_ratio` — fraction of tasks that went deterministic
- `ambiguous_llm_lift` — correctness of TCP+LLM vs TCP's `_select_best()` on
  ambiguous tasks (the number that proves the LLM earns its keep)

## Section 4: C3 Interface Seams

### Seam 1: RouteResult carries optional scoring data

```python
@dataclass(frozen=True)
class RouteResult:
    # ... existing fields ...
    confidence: RouteConfidence
    survivor_count: int
    candidate_scores: dict[str, float] | None = None  # tool_name → score
    score_gap: float | None = None  # top - runner-up
```

Today `candidate_scores` is always `None`. C3 populates it. The loop branch logic
changes from enum check to threshold check.

### Seam 2: Pluggable routing strategy

```python
def should_bypass_llm(result: RouteResult) -> bool:
    """Default strategy: bypass when exactly 1 survivor."""
    return result.confidence == RouteConfidence.DETERMINISTIC
```

Passed as a parameter to `run_agent_loop`. C3 swaps in a scoring-aware strategy
without touching loop internals.

### What C3 does NOT get designed now

The scoring model itself (semantic similarity? historical success? descriptor
specificity?). C2's ambiguous-task data will inform whether graduated scoring is
worth building.

## Section 5: Scope and Non-Goals

### In scope

- `RouteConfidence` enum + `survivor_count` + `candidate_scores` stub on `RouteResult`
- `should_bypass_llm` strategy function, default implementation
- Deterministic bypass path in agent loop (skip LLM, record `llm_bypassed=True`)
- 6-8 ambiguous tasks with synthetic tool records
- Three-lane benchmark reporting
- `ambiguous_llm_lift` metric

### Not in scope

- C3 scoring model — just the interface seams
- Changes to bitmask filter or gating logic
- Production serving / latency optimization
- Changes to existing 12 deterministic tasks
- MCP integration

### Success criteria

1. Deterministic tasks: bypass path matches LLM path correctness (100% = 100%)
2. Ambiguous tasks: TCP+LLM correctness > TCP's `_select_best()` alone
3. `bypass_ratio` reported — shows what % of workload skips the LLM
4. No regressions on existing MT-3 through MT-7 benchmarks

### Risk

If ambiguous tasks are badly designed (filter still narrows to 1, or admits too many
tools), the experiment is inconclusive. Mitigation: validate survivor counts per
ambiguous task before running the full benchmark. Target: 2-5 survivors.
