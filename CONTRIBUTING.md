# Contributing

Use Python 3.12 and the locked uv environment. Before submitting a change, run
the complete procedure in [the development runbook](docs/operations/development.md).

Keep domain, application, and presentation modules independent of Flet. New
external effects require a typed application port, an infrastructure adapter,
offline tests, and a security review.

GitHub Actions changes must also pass:

```powershell
actionlint .github/workflows/ci.yml
pinact run --check --min-age 7
```
