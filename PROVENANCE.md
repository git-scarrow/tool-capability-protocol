# Provenance

This repository predates public discussion of Anthropic's March 31, 2026 Claude Code source leak.

The point of this note is narrow: document the local, auditable timeline for `tool-capability-protocol` and record what can and cannot be inferred from the later overlap in architecture and naming.

## Timeline

The local git history for this repository shows:

- `c3e58de` on 2025-07-03: `Initial commit: TCP project setup`
- `9b30508` on 2025-07-03: `Update project documentation and add research findings`
- `d20b8c3` on 2025-07-03: `Add TCP performance benchmark suite for scientific validation`
- `bce2f33` on 2025-07-03: `Integrate TCP-MCP Protocol Bridge into main TCP research project`
- `f223707` on 2025-08-26: `feat(mcp): Complete MCP Registry with security hardening and performance optimizations`
- `9285f53` on 2025-08-26: `feat(tcp): Implement TCP v0.1 with lossless descriptors and adapters`
- `e63c3fd` on 2025-08-27: `Add evidence and policy TLVs, SNF, and selftest`
- `8ef9752` on 2025-09-09: merge of the evidence/policy TLV work into `main`

The technical specification is also dated July 3, 2025 in `TCP_SPECIFICATION.md`.

## What Predated The Leak Discussion

Before March 31, 2026, this repository already contained the following ideas:

- Compact tool-capability descriptors intended to replace repeated documentation parsing
- Binary encodings for capability, safety, and performance metadata
- Hierarchical compression for tool families such as `git`, `docker`, and `kubectl`
- Registry and discovery layers for filtering and selecting tools by capabilities
- Approval-gated and sandboxed tool availability for agents
- Policy and evidence encoded as structured protocol elements

These themes are visible in the repository history and in the protocol/specification documents dated mid-2025.

## Overlap With Later Claude Leak Summaries

A March 31, 2026 Reddit post summarizing the Claude Code leak highlighted three ideas that are notably adjacent to this repository's design:

- caching tool schemas to reduce prompt overhead
- filtering available tools based on gates and runtime context
- treating tool capability metadata as a significant part of the agent harness

This repository's prior work overlaps strongly at the architectural level, especially around:

- compact capability representations
- cached capability surfaces
- filtered tool availability
- policy-aware execution

## Limits Of The Claim

This note does **not** claim that Anthropic copied this repository.

What the local evidence supports is narrower:

- this repository existed substantially earlier
- the core architecture was already pointed at the same class of problems
- the name overlap and design overlap are real enough to document

The strongest defensible interpretation is independent convergence on a similar abstraction boundary: treating tool capabilities, policy, and execution constraints as first-class protocol data instead of leaving them as bulky prompt text or ad hoc runtime logic.

## Verification

This note is based on the local git history of this repository, not on memory alone.

Recommended verification commands:

```bash
git log --reverse --date=short --pretty=format:'%h %ad %s' | head -n 15
git log --all --date=short --pretty=format:'%h %ad %an %s' | rg '2025-08|2025-09'
git show c3e58de --stat
git show 9285f53 --stat
git show e63c3fd --stat
```
