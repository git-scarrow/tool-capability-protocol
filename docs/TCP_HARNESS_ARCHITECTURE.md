# TCP Harness Architecture

## Status

- Work Item: `TCP-DS-1`
- Status: Draft implementation spec
- Scope: First practical harness for descriptor-native tool routing in this repo

## Purpose

This document defines the first implementation slice for a TCP-gated harness.
The goal is not to redesign the entire protocol. The goal is to establish a
working harness that proves the core claim of this repository:

1. Tool selection and safety filtering should run on compact TCP descriptors.
2. Rich schema or prose should stay off the hot path.
3. The model should only see the reduced, already-approved tool surface.

This is the missing bridge between the existing TCP specification,
registry/discovery code, and the benchmark claims in `README_BENCHMARK.md`.

## Primary Execution Path

The primary live slice for the prototype is the benchmark harness path, not the
Docker/demo path.

Reason:

- The project hypothesis is about descriptor-native gating versus schema-heavy
  exposure.
- The benchmark path is the shortest route to a measurable result.
- The Docker/demo artifacts remain useful, but they are secondary until the
  harness can execute the same task set under both exposure models.

## Active Surface

This spec applies to the active repo surface defined in `ACTIVE_SURFACE.md`:

- `tcp/`
- `tests/`
- root packaging and protocol/spec files

Supporting but non-primary surfaces:

- `docs/`
- `examples/`
- `mcp-server/`
- `conformance/`

Out of scope for the first harness implementation:

- `consortium/`
- `tcp-knowledge-base/`
- `tcp-server-full/`
- `tcp_security_demo/`
- `langchain-integration/`
- `mcp-registry/`
- `tcp_v01/`

## Problem Statement

Traditional tool harnesses pay repeated cost in three places:

1. They serialize large JSON schemas and descriptions into the model context.
2. They force the runtime to interpret text-heavy capability surfaces each turn.
3. They mix policy, discovery, and execution in a way that makes gating slow and
   inconsistent.

TCP already has the pieces to avoid this:

- binary and structured capability descriptors in `tcp/core/descriptors.py`
- discovery and selection in `tcp/core/protocol.py`
- policy and evidence TLVs in `tcp/core/tlv_policy.py` and
  `tcp/core/tlv_evidence.py`
- sandboxed execution and capability caching in
  `tcp/security/secure_tcp_agent.py` and `tcp/security/sandbox_manager.py`
- a descriptor source in `mcp-server/tcp_database.py`

What is missing is a clear harness contract that makes those pieces cooperate.

## Design Goals

1. Keep the decision hot path descriptor-native.
2. Make safety filtering deterministic before prompt construction.
3. Allow richer metadata, policy, and evidence on a cold path only when needed.
4. Preserve compatibility with existing `CapabilityDescriptor` structures during
   the prototype.
5. Produce benchmarkable outputs against a schema-heavy baseline.

## Non-Goals

1. Do not solve canonical registry federation in the first prototype.
2. Do not redesign the full TCP binary format during harness implementation.
3. Do not require every existing descriptor to be rewritten before the harness
   can run.
4. Do not make the model reason over raw TLV payloads.

## System Model

The prototype harness has five layers:

1. Descriptor source
2. Registry and normalization
3. Pre-prompt gating
4. Model-visible tool projection
5. Execution and audit

### 1. Descriptor Source

The harness accepts descriptors from two sources:

- `CapabilityDescriptor` instances via `ToolCapabilityProtocol`
- raw binary descriptors from `TCPDescriptorDatabase`

During the prototype, both are normalized into a single in-memory
`ToolRecord` model.

### 2. Registry and Normalization

The harness owns a registry snapshot for the current run. Each `ToolRecord`
contains:

- `tool_name`
- `descriptor_version`
- `capability_flags`
- `commands`
- `formats`
- `processing_modes`
- `performance`
- `permission_level`
- `policy_tlvs`
- `evidence_tlvs`
- optional `rich_metadata`

Normalization rules:

1. Binary flags are authoritative for hot-path gating.
2. Structured command/format data is allowed on the hot path only after it has
   been normalized into compact fields.
3. Free-form descriptions are never required for allow/deny decisions.

### 3. Pre-Prompt Gating

This is the central harness step.

Inputs:

- task request
- runtime environment
- available tool records
- active policy set

Outputs:

- `candidate_tools`
- `approved_tools`
- `rejected_tools`
- audit log explaining the reason for each decision

Pre-prompt gating occurs in this order:

1. Availability filter
   - tool installed or descriptor present
2. Environment filter
   - local-only, network-enabled, file-access, sandbox class
3. Policy filter
   - deny/allow/approval requirements from `PolicyTLV` and sandbox policy
4. Capability filter
   - formats, commands, processing modes, required flags
5. Optimization pass
   - choose best surviving tools by speed or other declared criteria

No model prompt is constructed until `approved_tools` is finalized.

### 4. Model-Visible Tool Projection

The model must not receive the full registry.

Instead, the harness projects each approved tool into a compact view with only
the fields needed for planning:

- tool name
- a short capability summary
- accepted inputs
- important constraints
- approval requirement if any

This projection is the comparison point against a schema-heavy baseline.

The baseline path exposes traditional JSON-style tool definitions.
The TCP path exposes only the compact projection derived from descriptors.

### 5. Execution and Audit

Execution remains mediated by the sandbox layer.

Required behavior:

1. The model can request only tools in `approved_tools`.
2. Execution is revalidated at call time against sandbox policy.
3. The harness records:
   - why the tool survived gating
   - why alternatives were rejected
   - what policy or descriptor fields were consulted
   - whether execution succeeded, failed, or required escalation

## Hot Path vs Cold Path

### Hot Path

The hot path must be constant-time or near-constant-time relative to descriptor
size. It includes:

- bitmask checks on capability and risk flags
- simple command and format membership tests
- sandbox permission lookup
- environment compatibility checks
- ranking on precomputed performance fields

Hot path data should fit in memory without parsing rich JSON blobs at request
time.

### Cold Path

The cold path handles data that should not be needed on every turn:

- long descriptions
- provenance notes
- evidence payload inspection
- registry synchronization
- descriptor generation or repair
- human-readable debugging output

The harness may pull cold-path data only when:

1. a descriptor is incomplete
2. the user explicitly requests detail
3. a policy or execution failure requires diagnosis

## Canonical ToolRecord Contract

The prototype should introduce a small canonical data model in code, even if it
is internal-only at first.

Minimum fields:

```python
@dataclass
class ToolRecord:
    tool_name: str
    descriptor_source: str
    descriptor_version: str
    capability_flags: int
    risk_level: str
    commands: frozenset[str]
    input_formats: frozenset[str]
    output_formats: frozenset[str]
    processing_modes: frozenset[str]
    permission_level: str
    avg_processing_time_ms: float
    memory_usage_mb: float
    policy_tlvs: tuple[bytes, ...]
    evidence_tlvs: tuple[bytes, ...]
    rich_metadata: dict[str, object]
```

Prototype note:

- `risk_level` may be stored redundantly for readability, but it must be derived
  from flags or canonical descriptor data, not hand-maintained prose.

## Policy Model

Policy resolution order for the first prototype:

1. sandbox hard deny
2. explicit policy TLV deny
3. missing required environment capability
4. approval-required policy
5. allow

If sources disagree, the more restrictive rule wins.

This means the harness follows a default-deny posture when the descriptor or
runtime state is ambiguous.

## Evidence Model

`EvidenceTLV` is not part of the per-turn selection hot path. It is attached for:

- auditability
- benchmark traceability
- descriptor provenance
- later descriptor correction

The harness should carry evidence references forward in audit logs, but it does
not need to decode every evidence entry before making an allow/deny decision.

## Fallback Rules

The first prototype needs explicit fallback behavior because the repo contains a
mix of mature and partial implementations.

### Fallback 1: Missing Rich Metadata

If the binary or normalized descriptor is sufficient for gating, proceed without
rich metadata.

### Fallback 2: Missing Binary Descriptor

If only a `CapabilityDescriptor` exists:

1. normalize its structured fields
2. derive capability flags
3. mark `descriptor_source = "structured-only"`

### Fallback 3: Incomplete Security Signal

If risk or destructive/network state cannot be derived confidently:

1. mark tool as requiring approval
2. keep it off the auto-approved set

### Fallback 4: Descriptor Parse Failure

If parsing fails:

1. exclude the tool from auto-selection
2. record the failure in the audit log
3. allow manual inspection on the cold path only

## Prototype Modules

The first implementation should introduce a narrow module set under `tcp/`:

- `tcp/harness/models.py`
  - canonical `ToolRecord`, gating result types
- `tcp/harness/normalize.py`
  - adapters from `CapabilityDescriptor` and binary descriptors
- `tcp/harness/gating.py`
  - pre-prompt filtering and approval logic
- `tcp/harness/projection.py`
  - compact model-visible tool view
- `tcp/harness/router.py`
  - task-to-tool selection using the approved set
- `tcp/harness/audit.py`
  - decision log structures

These modules should depend on existing core and security code, not duplicate
them.

## Integration Plan

### Phase 1: Normalization

Build a canonical `ToolRecord` pipeline from:

- `ToolCapabilityProtocol.registry`
- `TCPDescriptorDatabase.descriptors`
- `SecureTCPAgent.capability_cache`

Success condition:

- one test can load tool records from current repo structures without requiring
  optional demo surfaces

### Phase 2: Gating

Implement deterministic filtering before prompt construction.

Success condition:

- tests show the same environment and task always yield the same approved tool
  set

### Phase 3: Projection

Generate compact model-visible tool summaries from approved tool records.

Success condition:

- token footprint is materially lower than the schema-heavy baseline for the same
  tool set

### Phase 4: Benchmark Harness

Run paired evaluations:

- schema-heavy exposure path
- TCP projection path

Measure:

- prompt size
- selection latency
- execution success
- false approvals
- false rejections

## Benchmark Contract

The benchmark should answer four questions:

1. How much prompt/context reduction does the TCP projection achieve?
2. How much runtime filtering latency is removed before the model sees tools?
3. Does compact projection preserve task completion quality?
4. Does deterministic gating reduce unsafe exposure compared with baseline?

Minimum benchmark artifact set:

- benchmark input tasks
- descriptor-backed tool corpus
- baseline tool schema corpus
- per-run audit logs
- result summary with methodology

## Invariants

These must hold for the prototype:

1. No tool reaches the model-visible set before policy and environment gating.
2. Approval requirements survive projection and execution.
3. Rich metadata is optional for the hot path.
4. Parse failures fail closed.
5. The same descriptor and environment yield the same gating result.

## Open Questions

These are real but non-blocking for the first implementation:

1. Whether command-level deltas should become first-class in the harness model or
   remain flattened for the prototype
2. Whether `PolicyTLV` should compile into bitmasks for the fastest hot path
3. Whether `EvidenceTLV` should remain opaque bytes in the harness core
4. Whether the benchmark baseline should use MCP-style schemas, OpenAI-style tool
   schemas, or the current repo's structured descriptors rendered as JSON

## Recommended Next Work

After this spec, the next implementation sequence should be:

1. `TCP-IMP-2`: build `tcp/harness/` normalization and gating modules
2. `TCP-MT-1`: define and run the schema-heavy versus TCP projection benchmark
3. `TCP-RV-1`: review descriptor invariants, fallback behavior, and policy edge
   cases

## Exit Criteria for TCP-DS-1

This work item is complete when:

1. the repo has a concrete harness architecture document
2. the document names the primary live slice
3. the hot path, cold path, and fallback rules are explicit
4. the spec can directly drive implementation without additional project-level
   planning
