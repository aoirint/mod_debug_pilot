# Continuous integration

## Contract

`.github/workflows/pull-request.yml` validates pull requests and merge-queue
groups. `.github/workflows/main.yml` re-runs the same lint and Python test checks
for each exact `main` commit, then makes the Windows build depend directly on
both jobs. Neither workflow has a manual-dispatch
surface because no diagnostic or recovery input contract is currently defined.

The Linux source jobs use the explicit `ubuntu-24.04` image rather than
`ubuntu-slim`: this project has not yet completed a representative slim-run
compatibility and resource assessment. Windows packaging uses the explicit
`windows-2025` image because it establishes the Windows artifact contract. All
jobs have read-only repository contents, and pull-request
concurrency cancels superseded proposed-source runs. No job receives release or
signing credentials.

The job verifies:

- the uv lock and exact Python environment;
- Ruff lint and formatting;
- strict mypy;
- 100% Python statement and branch coverage;
- SaveRedirect provenance-lock and embedded-DLL digest equality; and
- wheel and sdist construction, Windows Flet packaging, packaged Python/CLI/
  Flutter Flet version parity, executable startup, archive inspection, SHA-256
  manifest generation, and retention of the exact `main` build output.

The reusable local Composite Actions own only same-runner setup/check sequences:
Python setup, workflow lint tools, source linting, and Python tests. Workflow
files retain event ownership, runner selection, permissions, direct `needs`
gates, and artifact retention.

## Local workflow checks

```powershell
actionlint -color
pinact run --check --min-age 7
```

`actionlint` validates workflow syntax and expressions. `pinact` requires full
action SHAs and a minimum seven-day age. ShellCheck has no target because the
workflow and repository contain no maintained shell script.

Repository branch protection, Actions allow-list settings, private vulnerability
reporting, and release controls cannot be verified from this local repository.
Review those settings before claiming release readiness.

The `main` artifact is named with its source commit and includes
`ci-artifacts.json`, recording file SHA-256 values plus the uv and packaged
Flet versions. The Windows job launches the generated executable with isolated
application-data directories and requires the initial Agent page title within
30 seconds. A successful build without this runtime handshake is rejected. The
bundle is a validation artifact only; no release, signing, or publication flow
is defined.

Update this runbook when triggers, permissions, runner, action pins, commands,
repository settings, artifact policy, or release responsibilities change.
