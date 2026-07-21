# Dependency baseline

## Python graph

`uv.lock` resolves 72 packages from PyPI with hashes and upload timestamps. The
project-wide seven-day cooldown is stored as `exclude-newer = "P7D"`.

Direct runtime dependencies:

- `flet[desktop]` 0.85.3 provides the native Agent runtime and desktop client.
- `flet-web` 0.85.3 provides the exported browser-session application.
- `aiohttp` provides the bounded Thunderstore HTTPS client.
- Pillow 12.3.0 provides primary-display capture.
- PyYAML provides safe parsing of r2modman `export.r2x` metadata.
- Uvicorn provides the Agent-owned trusted-LAN HTTP ASGI listener.

Direct developer dependencies are Flet CLI 0.85.3, mypy 1.20.2, pytest 9.1.1,
pytest-cov 7.1.0, Ruff 0.14.14, and PyYAML type stubs. `flet`,
`flet-desktop`, `flet-web`, and `flet-cli` are fixed to the same exact version.
`flet build` resolves packaged Python dependencies from the declared project
requirements rather than reproducing `uv.lock`, so a compatible range can
otherwise put a newer Python server beside an older Flutter client and prevent
the first page from mounting. `uv.lock` owns the local and CI environment
graph; the final bundle is inspected separately for matching Python, CLI, and
Flutter Flet versions.

Dependencies cannot update outside an intentional lock review and the
seven-day cooldown.

## SaveRedirect artifact

`src/mod_debug_pilot/assets/save_redirect.lock.json` pins SaveRedirect 0.1.0 by
repository, full source commit, runtime contract, file name, and SHA-256 digest.
The application revalidates the lock and DLL before creating a profile. The
independent SaveRedirect repository owns its source graph, NuGet locks, tests,
package validation, and Lethal Company v81 evidence.

## GitHub Actions

The CI workflow uses only:

- `actions/checkout` v7.0.0 at
  `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0`;
- `astral-sh/setup-uv` v8.1.0 at
  `08807647e7069bb48b6ef5acd8ec9567f424441b`; and
- `actions/upload-artifact` v6.0.0 at
  `b7c566a772e6b6bfb58ed0dc250532a479d7789f`.

All references are full commit SHAs from their official GitHub repositories and
were older than seven days when adopted on 2026-07-20. `pinact` enforces the age
and pin policy.

Update this document after any direct dependency constraint, lock graph, action
reference, package source, or runtime-permission change.
