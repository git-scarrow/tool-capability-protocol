# Active Surface

This repository contains both the active TCP implementation surface and a large
amount of historical/demo/research material. This file defines the practical
working boundary for current development.

## Canonical Active Surface

These paths are the primary implementation surface for current TCP work:

- `tcp/`
  - Core protocol, descriptors, routing, security, generators, CLI helpers
- `tests/`
  - Main pytest suite for the active Python package
- `pyproject.toml`
  - Canonical Python packaging and test configuration
- `README.md`
  - Top-level project overview
- `TCP_SPECIFICATION.md`
  - Protocol and descriptor reference

## Secondary But Relevant

These paths are still relevant to the project, but are not the default place to
start for implementation work:

- `examples/`
  - Usage examples
- `docs/`
  - Supporting documentation and media
- `mcp-server/`
  - MCP bridge/server work related to TCP concepts
- `conformance/`
  - Conformance-oriented material

## Historical / Demo / Archive Surface

These paths are preserved for provenance, experiments, or older demos. They
should not drive new implementation work unless a specific task explicitly
targets them.

- `consortium/`
- `tcp-knowledge-base/`
- `tcp-server-full/`
- `tcp-demo-controlled/`
- `tcp_security_demo/`
- `secure_demo_sandbox/`
- `security_test_sandbox/`
- `langchain-integration/`
- `mcp-registry/`
- `tcp_v01/`
- root-level one-off demo scripts such as `tcp_*demo*.py`, analysis snapshots,
  simulation outputs, and generated artifacts

## Cleanup Principles

- Preserve provenance and historically important artifacts.
- Prefer narrowing the active development boundary over deleting old material.
- Fix active-surface breakage before reorganizing secondary or archival paths.
- Treat missing optional dependencies as optional whenever possible; the core
  package should remain importable without every experimental extra installed.

## Current Repo-Ready Baseline

As of this cleanup pass:

- `pytest -q` passes on the active test suite
- `import tcp` works without requiring optional CBOR dependencies
- The active package/test surface is the `tcp/` + `tests/` + root packaging/spec
  layer

## Known Remaining Rough Edges

- CLI entry points still require installed runtime dependencies such as `click`
- The repository layout is still broad and historically accreted
- The active boundary is documented here, but not yet enforced structurally
  through packaging splits or archival moves
