# TCP Harness Benchmark

## Status

- Work Item: `TCP-MT-1`
- Status: First local measurement track
- Scope: Compare schema-heavy tool exposure against TCP-gated projection using
  the first harness slice

## Purpose

This benchmark isolates the effect of representation and gating strategy inside
 the local harness.

It does not compare TCP against external LLM APIs. It compares two ways of
feeding the same tool corpus into a planner:

1. `schema-heavy`
   - serialize structured descriptors into JSON
   - reparse JSON at decision time
   - filter and select from that parsed structure
2. `tcp-projection`
   - normalize descriptors into `ToolRecord`
   - gate tools before prompt construction
   - expose only the compact projected approved set

The benchmark is designed to satisfy the kill condition for `TCP-MT-1`:
both paths use the same task requirements and deterministic selection rules, so
the observed difference is attributable to representation and gating rather than
unrelated prompt changes.

## Implementation

Code:

- `tcp/harness/benchmark.py`
- `tests/unit/test_harness_benchmark.py`

Key measured fields:

- prompt bytes
- prompt characters
- gating latency
- selection latency
- approved tool count
- rejected tool count
- approval-required tool count
- selected tool
- task satisfied

## First Local Run

Representative task:

- tool corpus:
  - `fast-json`
  - `slow-json`
  - `curl-json`
- task:
  - require command `transform`
  - require input format `json`
  - prefer fastest matching tool

Observed local output:

```text
json transform
schema_bytes 1102
tcp_bytes 473
schema_selected fast-json
tcp_selected fast-json
summary {'task_count': 1, 'mean_prompt_bytes_reduction': 629, 'mean_gating_latency_delta_ms': 0.02390299050603062, 'tcp_tasks_satisfied': 1, 'schema_tasks_satisfied': 1}
```

Interpretation:

- TCP projection reduced the prompt-facing payload from `1102` bytes to `473`
  bytes for the same task.
- Both paths selected the same correct tool, `fast-json`.
- The benchmark preserved task quality while reducing exposure size.
- Gating latency also improved, though the absolute local delta is small because
  the test corpus is intentionally tiny.

## Reproduction

Run the focused tests:

```bash
pytest -q tests/unit/test_harness_benchmark.py
```

Run a local benchmark snippet:

```python
from tcp.core.descriptors import CapabilityDescriptor, CommandDescriptor, FormatDescriptor
from tcp.core.descriptors import FormatType, PerformanceMetrics, ProcessingMode, CapabilityFlags
from tcp.harness import BenchmarkTask, RuntimeEnvironment, ToolSelectionRequest
from tcp.harness import benchmark_exposure_paths, summarize_comparisons

def descriptor(name, command, latency, flags=0):
    return CapabilityDescriptor(
        name=name,
        version="1.0",
        commands=[CommandDescriptor(name=command)],
        input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        output_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        processing_modes=[ProcessingMode.SYNC],
        capability_flags=flags,
        performance=PerformanceMetrics(avg_processing_time_ms=latency, memory_usage_mb=8),
    )

descriptors = [
    descriptor("fast-json", "transform", 5),
    descriptor("slow-json", "transform", 50),
    descriptor("curl-json", "fetch", 15, int(CapabilityFlags.SUPPORTS_NETWORK)),
]

tasks = [
    BenchmarkTask(
        name="json transform",
        request=ToolSelectionRequest.from_kwargs(
            required_commands={"transform"},
            required_input_formats={"json"},
            preferred_criteria="speed",
            require_auto_approval=False,
        ),
        expected_tool_names=frozenset({"fast-json"}),
    )
]

comparisons = benchmark_exposure_paths(
    descriptors,
    tasks,
    RuntimeEnvironment(installed_tools=frozenset({"fast-json", "slow-json", "curl-json"})),
)

print(summarize_comparisons(comparisons))
```

## Limitations

This first track is intentionally narrow.

- It measures representation and local gating cost, not end-to-end model
  inference cost.
- It uses a small synthetic corpus instead of a full MCP or OpenAI-style tool
  schema surface.
- It does not yet include richer task-quality scoring beyond selected-tool
  correctness.

## Next Measurement Step

Expand the corpus and task set while preserving benchmark isolation:

1. add file, stream, and approval-required tasks
2. compare against a larger schema-heavy baseline surface
3. record repeated runs and aggregate summary statistics
4. feed resulting edge cases into `TCP-RV-1`
