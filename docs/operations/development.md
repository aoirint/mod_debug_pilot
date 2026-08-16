# Development

## Prerequisites

- Git
- uv 0.12.3 or a compatible newer version
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
apm audit --ci
actionlint -color
pinact run --check --min-age 7
```

The Python result must have 100% statement and branch coverage. Verify that
`src/mod_debug_pilot/assets/com.aoirint.SaveRedirect.dll` matches the SHA-256 in
`save_redirect.lock.json`; runtime profile creation and CI reject a difference.

These commands must not update `uv.lock`. Delete `.venv` and other build outputs
and repeat the exact sync if environment drift is suspected. Build, coverage,
profiles, runs, and artifacts are disposable and ignored by Git.

Inspect both artifacts after `uv build`. The wheel must include the embedded
SaveRedirect artifact and provenance lock while excluding tests, local settings,
credentials, caches, and run data. The sdist intentionally contains source
tests, the pinned dependency, and developer documentation.

Windows packaging and signing remain blocked until product identifiers,
certificate ownership, and release channels are selected.

Update this runbook when Python, uv, the SaveRedirect pin, quality gates, or
packaging policy changes.
