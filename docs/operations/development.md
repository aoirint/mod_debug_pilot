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
actionlint .github/workflows/ci.yml
pinact run --check --min-age 7
```

These commands must not update `uv.lock`. Delete `.venv` and repeat the exact
sync if environment drift is suspected. Generated `build/`, `dist/`, coverage,
profiles, runs, and artifacts are disposable and ignored by Git.

Windows packaging and signing remain blocked until product identifiers,
certificate ownership, and release channels are selected.

The expected test result is 100% statement and branch coverage. Inspect both
artifacts after `uv build`. The wheel must not contain tests, local settings,
credentials, caches, or generated run data. The sdist intentionally contains
source tests and developer documentation, but must exclude local settings,
credentials, caches, generated run data, and source-control metadata.

Update this runbook when Python, uv, quality gates, or packaging policy changes.
