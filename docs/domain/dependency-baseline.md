# Dependency baseline

## Python graph

`uv.lock` resolves 75 packages from PyPI with hashes and upload timestamps. The
project-wide `P7D` cooldown is stored as `exclude-newer-span = "P7D"`.

Direct runtime dependencies:

- `flet[desktop]` 0.85.3 provides the native Agent runtime and desktop client.
- `flet-web` 0.85.3 provides the exported browser-session application.
- `aiohttp` provides the bounded HTTPS API and Thunderstore client.
- `cryptography` provides RSA TLS identities and Ed25519 request signatures.
- Pillow 12.3.0 provides primary-display capture.
- PyYAML provides safe parsing of r2modman `export.r2x` metadata.
- Uvicorn provides the Agent-owned trusted-LAN HTTP ASGI listener.

Direct developer dependencies are Flet CLI 0.85.3, mypy 1.20.2, pytest 9.1.1,
pytest-cov 7.1.0, Ruff 0.14.14, and PyYAML type stubs. Flet CLI is explicit
because the `flet` launcher otherwise installs its CLI surface at runtime. The
complete transitive graph and artifact hashes are owned by `uv.lock`; this
document does not duplicate them.

Dependencies cannot update outside an intentional lock review and the
seven-day cooldown.

## Save redirector graph

`global.json` selects .NET SDK 10.0.201 with patch-only roll-forward. The helper
targets .NET Standard 2.1 and locks BepInEx.Core 5.4.21,
LethalCompany.GameLibs.Steam 81.0.5-ngd.0, UnityEngine.Modules 2022.3.62,
BepInEx.Analyzers 1.0.8, and BepInEx.PluginInfoProps 2.1.0 plus their transitive
graphs. `nuget.config` clears inherited sources and maps packages between the
official NuGet and BepInEx feeds. Restore uses `--locked-mode`.

## GitHub Actions

The CI workflow uses only:

- `actions/checkout` v7.0.0 at
  `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0`;
- `astral-sh/setup-uv` v8.1.0 at
  `08807647e7069bb48b6ef5acd8ec9567f424441b`; and
- `actions/setup-dotnet` v5.4.0 at
  `26b0ec14cb23fa6904739307f278c14f94c95bf1`.

All references are full commit SHAs from their official GitHub repositories and
were older than seven days when adopted on 2026-07-20. setup-dotnet v6.0.0 was
intentionally not adopted because its tag was less than seven days old. `pinact`
enforces the age and pin policy.

Update this document after any direct dependency constraint, lock graph, action
reference, package source, or runtime-permission change.
