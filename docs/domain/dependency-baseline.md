# Dependency baseline

## Python graph

`uv.lock` resolves 49 packages from PyPI with hashes and upload timestamps. The
project-wide `P7D` cooldown is stored as `exclude-newer-span = "P7D"`.

Direct runtime dependencies:

- `flet[desktop]` 0.85.3 provides the UI runtime and desktop client.
- Pillow 12.3.0 provides primary-display capture.

Direct developer dependencies are Flet CLI 0.85.3, mypy 1.20.2, pytest 9.1.1,
pytest-cov 7.1.0, and Ruff 0.14.14. Flet CLI is explicit because the `flet`
launcher otherwise installs its CLI surface at runtime. The complete transitive
graph and artifact hashes are owned by `uv.lock`; this document does not
duplicate them.

The PyPI Flet project is maintained under the verified `flet` publisher entry,
uses the Apache-2.0 license, and publishes a universal Python wheel. Pillow is
maintained by the Python Imaging Library project and includes native Windows
wheels. Neither dependency is allowed to update outside an intentional lock
review and the seven-day cooldown.

## GitHub Actions

The CI workflow uses only:

- `actions/checkout` v7.0.0 at
  `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0`.
- `astral-sh/setup-uv` v8.1.0 at
  `08807647e7069bb48b6ef5acd8ec9567f424441b`.

Both references are full commit SHAs from their official GitHub repositories
and were older than seven days when adopted on 2026-07-20. `pinact` enforces the
age and pin policy.

Update this document after any direct dependency constraint, lock graph, action
reference, package source, or runtime-permission change.
