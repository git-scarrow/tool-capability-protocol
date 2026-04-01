# TCP Harness Benchmark MT-2

## Status

- Work Item: `TCP-MT-2`
- Status: Expanded measurement track
- Scope: Broader fixture set with multi-tool routing, repeated runs, and false
  allow / false rejection accounting

## Purpose

This measurement track extends `TCP-MT-1`.

Where `TCP-MT-1` proved the first narrow comparison, `TCP-MT-2` expands the
fixture set while keeping the comparison controlled:

1. baseline and TCP paths use the same task requirements
2. selection criteria stay deterministic
3. only the representation and gating surfaces differ

## Implementation

Code:

- `tcp/harness/benchmark.py`
- `tests/unit/test_harness_benchmark.py`

New benchmark elements:

- repeated suite execution via `benchmark_exposure_suite()`
- broader fixtures via `build_mt2_fixture_set()`
- multi-tool routing scenarios
- file, stream, network-blocked, and approval-guarded tasks
- false allow and false rejection accounting

## Fixture Set

Representative tools:

- `fast-json`
- `slow-json`
- `stream-json`
- `file-convert`
- `net-fetch`
- `priv-admin`

Representative tasks:

1. fast local transform
2. offline stream transform
3. binary file convert
4. auto approval guarded

## Measured Local Result

Repeated run:

- repetitions: `5`
- task count per repetition: `4`
- total comparisons: `20`

Observed summary:

```text
{
  'task_count': 20,
  'mean_prompt_bytes_reduction': 1856,
  'mean_gating_latency_delta_ms': 0.058542349870549515,
  'tcp_tasks_satisfied': 15,
  'schema_tasks_satisfied': 20,
  'tcp_false_allows': 0,
  'schema_false_allows': 0,
  'tcp_false_rejections': 30,
  'schema_false_rejections': 0
}
```

## Interpretation

The expanded benchmark says three important things:

1. TCP projection still materially reduces prompt-facing payload size.
2. The harness remains conservative: it does not introduce false allows in this
   fixture set.
3. The current structured TCP path produces false rejections on the
   approval-guarded task because unknown structured safety semantics are treated
   as approval-required.

That third result is not a benchmark failure. It is the most important output of
`TCP-MT-2`.

It demonstrates that:

- the harness is currently safer than the schema-heavy baseline on ambiguous
  semantics
- the price of that safety is measurable over-conservatism
- the next optimization target is better structured safety signaling, not a
  weaker gate

## Kill/Stop Condition

Not triggered.

The expanded benchmark kept baseline and TCP conditions matched. The observed
false rejection behavior is attributable to the current TCP gating semantics, not
to unrelated prompt drift or implementation noise.

## Next Move

The next useful step after `TCP-MT-2` is not more generic benchmarking.
It is targeted refinement of structured safety semantics so the harness can
reduce false rejections without increasing false allows.
