# TCP Descriptor Review

## Status

- Work Item: `TCP-RV-1`
- Status: Review artifact
- Scope: Review descriptor invariants, versioning assumptions, TLV fit, and the
  smallest stable subset that can support the current harness

## Verdict

The prototype is viable.

The descriptor model is not stable enough to treat every historical encoding in
this repo as one unified protocol surface, but it is stable enough to support a
meaningful harness if the project freezes a narrower subset now.

The smallest stable subset is:

1. `ToolRecord` as the canonical harness model
2. `CapabilityDescriptor` as the primary structured source format
3. `BinaryCapabilityDescriptor` (`TCP\x01`, 20-byte) as a local compact runtime
   encoding
4. legacy 24-byte `TCP\x02` descriptors as compatibility inputs only
5. `PolicyTLV` and `EvidenceTLV` as cold-path attachments, not hot-path routing
   primitives

## Findings

### 1. The repo currently contains multiple protocol surfaces, not one

Observed surfaces:

- structured descriptors in `tcp/core/descriptors.py`
- compact 20-byte `BinaryCapabilityDescriptor` with magic `TCP\x01`
- legacy 24-byte `TCP\x02` descriptors in research and MCP bridge code
- hierarchical `TCP\x03` described in the spec but not implemented as the live
  harness encoding

Implication:

- version numbers in the repo are historically layered rather than cleanly
  superseding each other
- the harness should not pretend these are interchangeable

Decision:

- `ToolRecord` is the only live internal contract
- all older descriptor shapes are adapters into that contract

### 2. Capability flags are stable for hot-path capability filtering, but not for complete safety semantics

The current `CapabilityFlags` enum is strong for:

- file/stdin/network capability
- processing mode
- output shape
- operational hints such as stateless/idempotent/caching

It is weak for:

- destructive behavior
- privilege escalation
- system modification
- explicit approval semantics

Those richer security concepts exist in the legacy `TCP\x02` bitfield and in
the sandbox/policy layer, not in the current structured flag enum.

Implication:

- current flags are sufficient for capability routing
- they are insufficient as the only safety surface

Decision:

- keep capability flags on the hot path
- keep safety policy and approval semantics in sandbox policy and policy TLVs
- fail closed when structured descriptors cannot derive enough security signal

### 3. Policy TLVs fit the architecture, but not as mandatory per-turn parse inputs

`PolicyTLV` is a good fit for:

- deny/allow/approval rules
- environment gates
- provenance on why a tool is restricted

It is a bad fit as a required per-turn decoding step for every tool.

Decision:

- TLVs stay attached to records
- policy should compile into compact runtime checks before or during
  normalization when needed
- the harness should not decode arbitrary policy payloads during every routing
  decision

### 4. Evidence TLVs are valid, but purely off-path

`EvidenceTLV` is useful for:

- auditability
- provenance
- later correction of descriptor claims
- benchmark traceability

It should not participate in routing or selection.

Decision:

- evidence remains opaque to the hot path
- the harness may carry evidence references in audit logs only

### 5. The serializer surface was less stable than the descriptor surface

The review surfaced a concrete defect in `CapabilityDescriptor.to_dict()`:
`IntEnum` values were serialized after the generic `__dict__` branch, which
caused recursive serialization failure in schema-heavy benchmark paths.

Action taken:

- fixed the serializer ordering so `IntEnum` values serialize directly

Implication:

- schema-heavy baseline comparisons are now less brittle
- this was a serializer bug, not evidence that the descriptor model itself is
  unusable

## Stable Subset

The following should be treated as frozen for the prototype cycle:

### Canonical Harness Model

Use `ToolRecord` as the only runtime contract for:

- gating
- projection
- routing
- benchmark comparisons

### Accepted Input Encodings

Normalize these inputs into `ToolRecord`:

1. `CapabilityDescriptor`
2. `BinaryCapabilityDescriptor`
3. legacy 24-byte `TCP\x02` research descriptors

Do not route directly on any raw descriptor byte layout.

### Safety Posture

Freeze these rules:

1. unknown security semantics require approval
2. parse failure fails closed
3. environment and sandbox policy override descriptor optimism
4. policy disagreement resolves to the more restrictive result

### Off-Path Metadata

Keep these off the hot path:

- free-form descriptions
- provenance notes
- evidence entry contents
- registry sync metadata
- historical family/hierarchical compression concepts

## Unstable Areas To Defer

These should not be hardened into the prototype contract yet:

### 1. Unified binary version story

The repo still carries multiple binary stories:

- `TCP\x01` local compact runtime encoding
- `TCP\x02` research/security encoding
- `TCP\x03` hierarchical spec

Recommendation:

- do not collapse them prematurely
- document each as either live, compatibility, or aspirational

### 2. Hierarchical family encoding

The spec’s family compression model is promising, but it is not the current
active harness substrate.

Recommendation:

- leave it as a future optimization
- do not couple current routing correctness to family-level deltas

### 3. Rich security bit allocation inside structured capability flags

The current structured flags do not yet have a disciplined layout for:

- destructive operations
- privilege escalation
- root requirements
- system mutation

Recommendation:

- either add a separate explicit safety flag space later
- or formally keep those semantics in policy/sandbox layers

### 4. Generic full-fidelity descriptor export

The repo still lacks a clearly blessed, round-trippable, full-fidelity export
shape for every descriptor family.

Recommendation:

- keep using targeted snapshots for benchmark baselines
- avoid claiming that one generic serializer is the protocol

## Required Invariants

These invariants should hold from this point forward:

1. `ToolRecord` is the only harness-internal routing contract.
2. Raw descriptors are normalized before gating.
3. Capability flags never stand in for the full policy engine.
4. Evidence payloads never participate in hot-path allow/deny decisions.
5. Unknown or incomplete security signal requires approval or rejection.

## Recommended Next Moves

After this review, the next project moves should be:

1. expand the benchmark corpus with more task types and approval-required cases
2. decide whether safety semantics get a dedicated structured flag space or stay
   policy-only
3. document the protocol-status table explicitly:
   - live
   - compatibility
   - aspirational

## Kill/Stop Condition

Not triggered.

The review did not find that descriptor semantics are too unstable for a
meaningful prototype. It found that the live prototype must commit to a smaller
stable subset and adapter model, which is now explicit.
