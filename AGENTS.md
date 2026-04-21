# Repository Guidelines

## Project Structure & Module Organization
The main Python package lives in `tcp/`, split by concern: `core/` for protocol primitives, `proxy/` for command-control proxy logic, `agent/` for agent workflows, `harness/` for benchmarking, and `security/` for sandboxing and approval flows. Root tests live in `tests/` with `unit/`, `integration/`, `reliability/`, `data/`, and `vectors/` subfolders. Supporting docs and demos are under `docs/`, `examples/`, and `docker/`. Two related subprojects, `mcp-server/` and `mcp-registry/`, maintain their own packaging and README files.

## Build, Test, and Development Commands
Use Poetry for the root package:

```bash
poetry install
poetry run pytest
poetry run pytest tests/unit -m "not slow"
poetry run black tcp tests && poetry run isort tcp tests
poetry run flake8 tcp tests && poetry run mypy tcp
```

`pytest` runs the main suite defined in `pyproject.toml`; CI also collects coverage with `--cov=tcp`. The root `Makefile` is for the Docker security demo (`make build`, `make run`, `make shell`), not the standard library workflow.

## Coding Style & Naming Conventions
Target Python 3.9+ in the root package. Use 4-space indentation, `snake_case` for modules/functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Format with Black (88 columns) and sort imports with isort using the Black profile. Keep new modules inside the existing domain folders instead of creating one-off top-level scripts.

## Testing Guidelines
Name tests `test_*.py` or `*_test.py`, and keep them close to the relevant area in `tests/unit` or `tests/integration`. Reuse existing markers such as `unit`, `integration`, `slow`, `security`, and `reliability_99999`. Run focused checks locally before opening a PR, for example `poetry run pytest tests/unit/test_router.py -q`.

## Commit & Pull Request Guidelines
Recent history follows short imperative subjects, often Conventional Commit style, for example `feat(proxy): ...`, `fix(derivation): ...`, or `style: ...`, sometimes with ticket IDs like `TCP-IMP-18`. Keep commits scoped and descriptive. PRs should summarize behavior changes, list validation commands run, link the relevant issue, and include screenshots only for UI or visualization changes.

## Configuration Notes
Avoid committing generated caches, virtualenv contents, or large artifacts. Treat `artifacts/`, `tcp-knowledge-base/`, and sandbox directories as potentially bulky or environment-specific unless the change explicitly belongs there.
