# Contributing

Use Python 3.12, the locked uv environment, and the .NET SDK selected by
`global.json`. Before submitting a change, run the complete procedure in
[the development runbook](docs/operations/development.md).

Keep domain, application, and presentation modules independent of Flet. New
external effects require a typed application port, an infrastructure adapter,
offline tests, and a security review.

Changes to the save redirector must preserve its plugin GUID, `BepInProcess`
scope, path confinement, fail-closed ready marker, locked NuGet graph, and exact
match between the Release output and embedded application DLL. Update the v81
evidence report when the game-library baseline changes.

GitHub Actions changes must also pass:

```powershell
actionlint .github/workflows/ci.yml
pinact run --check --min-age 7
```
