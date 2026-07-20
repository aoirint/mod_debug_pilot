# Development

## Prerequisites

- Git
- uv 0.11.21 or a compatible newer version
- Windows 11 for runtime UI and capture checks

## Setup and verification

From a clean clone:

```powershell
uv lock --check
uv sync --locked --all-groups
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src tests
uv run --locked pytest
uv build
```

These commands must not update `uv.lock`. Delete `.venv` and repeat the exact
sync if environment drift is suspected. Generated `build/`, `dist/`, coverage,
profiles, runs, and artifacts are disposable and ignored by Git.

Windows packaging and signing remain blocked until product identifiers,
certificate ownership, and release channels are selected.

Update this runbook when Python, uv, quality gates, or packaging policy changes.
