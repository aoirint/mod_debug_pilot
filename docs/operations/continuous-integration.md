# Continuous integration

## Contract

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, merge queue
groups, and manual dispatch. It uses `windows-latest`, a 20-minute timeout,
read-only repository contents, and concurrency cancellation for superseded pull
requests.

The job verifies the lock, synchronizes the exact environment, runs Ruff lint
and format checks, runs strict mypy, requires 100% statement and branch coverage,
and builds wheel and sdist distributions. It receives no release or signing
credential.

## Local workflow checks

```powershell
actionlint .github/workflows/ci.yml
pinact run --check --min-age 7
```

`actionlint` validates workflow syntax and expressions. `pinact` requires full
action SHAs and a minimum seven-day age. ShellCheck has no target because the
workflow and repository contain no maintained shell script.

Repository branch protection, Actions allow-list settings, private vulnerability
reporting, and release controls cannot be verified from this local repository.
Review those settings before claiming release readiness.

Update this runbook when triggers, permissions, runner, action pins, commands,
repository settings, or release responsibilities change.
