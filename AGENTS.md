# Project Mandates: Tool Capability Protocol (TCP)

## Core Operational Loop: TCP-CC Proxy
The absolute heart of this project is the **TCP-CC Proxy**, not the static binary protocol utilities. Any agent working in this repo must prioritize the live gating loop.

- **Primary Process**: `tcp.proxy.cc_proxy` (FastAPI sidecar).
- **Lifecycle**: Started via `scripts/tcp_proxy_ensure.sh` (Port 8742).
- **Primary Contract**: `tcp/derivation/request_derivation.py`. This logic derives required capabilities from prompts to gate tool visibility.
- **Ground Truth**: `artifacts/tcp-data-1/candidate_turns.jsonl` (50 hand-labeled turns).
- **Current State**: The derivation logic is **KILLED** (Failed validation) with **5.4% precision** in the TCP-VAL-1 audit.

## Immediate Development Priority
**Regression-Hardening of Request Derivation.**
Do not refactor the binary protocol or compression logic until `derive_request` precision meets the **80% pass-line** defined in TCP-DS-2.

## Critical Paths
- **Logs**: `~/.tcp-shadow/proxy/decisions.jsonl` (Inspect this to see live gating failures).
- **Validation**: `python3 tcp/derivation/audit_contract.py --audit-set artifacts/tcp-data-1/audit_set_ground_truth.jsonl`
