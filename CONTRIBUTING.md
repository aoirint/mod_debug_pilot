# Contributing

Use Python 3.12 and the locked uv environment. Before submitting a change, run
the complete procedure in [the development runbook](docs/operations/development.md).

Keep domain, application, and presentation modules independent of Flet. New
external effects require a typed application port, an infrastructure adapter,
offline tests, and a security review.

SaveRedirect source changes belong in its
[independent repository](https://github.com/aoirint/SaveRedirect). When adopting
a new build here, update the DLL and `save_redirect.lock.json` together, verify
the locked source commit, and preserve the plugin GUID, environment variable,
ready marker, and SHA-256 contract.

GitHub Actions changes must also pass:

```powershell
actionlint -color
pinact run --check --min-age 7
```

Pull requests validate proposed source through the `Pull Request` workflow.
The `Main` workflow re-runs those checks for the exact pushed commit before its
Windows build and retained artifact job. Do not add manual dispatch or a
cross-workflow polling gate without a documented diagnostic or recovery need.

## Reporting security issues

Use a private GitHub security advisory or another private maintainer channel
for suspected vulnerabilities. Do not publish credentials, machine paths,
exploit details, Steam account information, or other sensitive data.
