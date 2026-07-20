# Development

## Prerequisites

- Git
- uv 0.11.21 or a compatible newer version
- .NET SDK selected by `global.json`
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
dotnet restore ModDebugPilot.slnx --locked-mode
dotnet format ModDebugPilot.slnx --no-restore --verify-no-changes
dotnet build ModDebugPilot.slnx --configuration Release --no-restore --warnaserror
dotnet run --project helpers/ModDebugPilot.SaveRedirector.Tests --configuration Release --no-build
uv build
actionlint -color
pinact run --check --min-age 7
```

The Python result must have 100% statement and branch coverage. The .NET build
must have zero warnings, and the helper boundary executable must return zero.
Compare the SHA-256 of the Release helper output with
`src/mod_debug_pilot/assets/ModDebugPilot.SaveRedirector.dll`; CI rejects a
difference.

These commands must not update `uv.lock` or either `packages.lock.json`. Delete
`.venv`, `bin`, and `obj` outputs and repeat the exact restores if environment
drift is suspected. Those directories plus build, coverage, profiles, runs, and
artifacts are disposable and ignored by Git.

Inspect both artifacts after `uv build`. The wheel must include the embedded
save redirector and exclude tests, local settings, credentials, caches, and run
data. The sdist intentionally contains source tests, helper source, lockfiles,
and developer documentation.

Windows packaging and signing remain blocked until product identifiers,
certificate ownership, and release channels are selected.

Update this runbook when Python, .NET, uv, quality gates, or packaging policy
changes.
