# TCP-DS-3: ToolPackController (TPC) Design Spec

**Status**: Draft
**Date**: 2026-04-10
**Author**: Gemini CLI (Project: TCP Harness Prototype)
**Objective**: Define the minimal contract for a workspace-local ToolPackController (TPC) to ensure deterministic visibility of MCP tool families and prevent heuristic omission of legitimate tools.

## 1. Problem Statement

During `TCP-IMP-9` stabilization, we observed that legitimate tool families (e.g., `bay-view-graph` for email access) were frequently omitted from the agent's visible tool surface. This happened because:
1.  **Heuristic Omission**: The proxy's prompt-derived heuristics didn't see an "obvious" need for the tool and suppressed it.
2.  **Manifest Complexity**: The existing `.tcp-proxy-packs.yaml` logic, while powerful, lacked a clear boundary between "workspace-level visibility" and "hard security policy."
3.  **State Fragmentation**: Tool visibility was being decided by a mix of environment variables, manifest rules, and runtime heuristics, leading to non-deterministic behavior.

## 2. The TPC Contract

The ToolPackController (TPC) acts as the deterministic decision engine that prepares the tool surface *before* TCP performs descriptor-native gating.

### 2.1 Core Responsibilities
- **Deterministic Family State**: Map every installed MCP server to exactly one state: `ACTIVE`, `DEFERRED`, or `SUPPRESSED`.
- **Structural Visibility**: Guarantee that tools in the `ACTIVE` and `DEFERRED` states are sent to the upstream model (as full schemas or minimal descriptors, respectively).
- **Workspace Awareness**: Use local indicators (repo markers, `.tcp-proxy-packs.yaml`, `cwd`) to influence states.
- **Policy Enforcement**: Respect hard security "disables" which override any controller-level "active" request.

### 2.2 Decision Inputs
1.  **Workspace Manifest**: `.tcp-proxy-packs.yaml` (the source of truth for family groupings).
2.  **Environment Constraints**: `TCP_PROXY_ALLOWED_MCP_SERVERS`, `TCP_PROXY_WORKSPACE_PROFILE`.
3.  **Local Context**: The current working directory (CWD) and known "active" project names.
4.  **Hard Policy**: Central security configuration that defines which servers are "banned" or "restricted" regardless of workspace needs.

## 3. Tool States

| State | Visibility | Schema Materialization | Rationale |
| :--- | :--- | :--- | :--- |
| **ACTIVE** | Full | Materialized (Full JSON) | Tools likely needed for the current task/workspace. |
| **DEFERRED**| Limited | Compact (Minimal Descriptor) | Tools available but not immediately relevant; preserved for "rescue" mentions. |
| **SUPPRESSED**| None | None | Tools explicitly disabled or irrelevant to the entire workspace. |

## 4. Proposed Architecture

### 4.1 Separation of Concerns
- **TCP substrate**: Provides the 24-byte descriptors and the low-level gating logic.
- **TPC layer**: Orchestrates which families are projected into the session.

### 4.2 Handling the "bay-view-graph" Regression
To fix the issue where legitimate families are hidden:
1.  If a server is listed in the `active_workspaces` or `active_profiles` section of the manifest for the current context, it **MUST** be at least `DEFERRED` (structurally visible), never `SUPPRESSED`.
2.  Heuristics may "upgrade" a `DEFERRED` tool to `ACTIVE`, but they can **NEVER** "downgrade" an `ACTIVE`/`DEFERRED` tool to `SUPPRESSED`.

## 5. Implementation Roadmap

1.  **Refactor `resolve_pack_decisions`**: Move from ad-hoc logic in `cc_proxy.py` to a formal `ToolPackController` class in `tcp/proxy/controller.py`.
2.  **Formalize the "Safety Floor"**: Ensure core coding tools (read, edit, bash) are managed by the TPC as a permanent `ACTIVE` pack.
3.  **Strict Policy Boundary**: Add a `PolicyEngine` that the TPC queries before finalizing states.
4.  **Telemetry**: Enhance `decisions.jsonl` to record the exact TPC rule that determined a family's state.

## 6. Success Metrics

- **False Reject Rate**: 0% for families listed in the workspace's active packs.
- **Visibility Overhead**: Mean visible-tool count ≤ 60 even with >200 installed tools.
- **Config Burden**: Adding a new MCP family requires <5 lines of YAML in `.tcp-proxy-packs.yaml`.
