# Changelog

All notable development changes to ModDebugPilot are recorded here. This file
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- Added the native Agent and its operator-started HTTP Flet Web controller with
  one-time pairing and local approval.
- Added Thunderstore Profile Code import, bounded package resolution, local DLL
  selection, config editing, verified profile bundles, and Agent installation.
- Added tracked multi-instance launch, unique debugger ports, screenshot
  download, individual process termination, and structured artifacts.
- Added pinned SaveRedirect integration with per-instance Easy Save 3 roots,
  LCBetterSaves 1.7.3 path coverage, and journaled normal-save recovery.
- Added strict Ruff, mypy, 100% statement-and-branch coverage, Windows Flet
  packaging, archive inspection, and SHA-pinned GitHub Actions.
- Pinned and deployed the repository's Flet, game-analysis, cross-repository,
  and maintenance Skills through APM.

### Changed

- Consolidated remote control onto the single Agent-hosted Flet Web surface;
  removed the pre-release local-only runner and separate automation API.
- Aligned the Python package with the Flet architecture boundary:
  `domain`, `application`, `presentation`, `infrastructure`, `ui`,
  `composition`, and thin `entrypoints`.
- Moved SaveRedirect source, tests, package validation, and v81 save-path
  evidence to its independent repository. ModDebugPilot consumes only its
  commit- and SHA-256-pinned plugin artifact.
- Required keyword arguments for project-owned APIs while documenting callback
  signatures imposed by Flet, aiohttp, ASGI, asyncio, and Python.

### Fixed

- Fixed Windows CI toolchain parsing and forced UTF-8 output for Flet builds.
- Ensured listener-start failures shut down recovered runtime state.

### Security

- Restricted the HTTP surface with exact Host and same-origin WebSocket checks,
  an eight-digit single-use code, five-guess limit, and local approval.
- Restricted Thunderstore redirects to HTTPS Thunderstore hosts and bounded
  transfers, archives, uploads, configs, and artifacts.
- Rejected traversal, links, digest mismatches, unsafe identifiers, untracked
  game processes, and unapproved browser sessions.
- Required the SaveRedirect ready marker before an instance becomes active;
  failed isolation terminates the game and restores journaled state.
- Exposed structured operations only, with no arbitrary shell, URL, environment,
  registry, firewall, credential, or free-form launch-argument operation.

### Notes

- The controller requires only a browser; the Agent owns Python, profiles,
  package materialization, game processes, and recovery state.
- Browser traffic is unencrypted and requires a trusted private LAN plus a
  source-restricted firewall rule.
- Live-game verification of the v81 SaveRedirect patch remains a pre-release
  task; static evidence and fail-closed startup are implemented.
- Full-display screenshots can contain unrelated apps and notifications.
- Code signing and a stable release channel are not configured.
