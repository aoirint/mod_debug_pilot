# Changelog

All notable development changes to ModDebugPilot are recorded here. This file
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- Added an operator-started native Agent and trusted-LAN HTTP Flet Web controller
  requiring one-time pairing plus local approval.
- Added self-signed TLS identity management, certificate-pinned API clients,
  Ed25519 request signatures, timestamp freshness, and nonce replay protection.
- Added Thunderstore Profile Code import, bounded package resolution, local DLL
  upload, configuration editing, verified profile bundles, and Agent install.
- Added multiple tracked Lethal Company instances with unique debugger ports,
  screenshot download, individual task termination, and structured artifacts.
- Added a v81 BepInEx helper that injects a confined per-instance ES3 save root,
  plus journaled normal-save and Doorstop recovery.
- Added boundary coverage for the ES3 save, auxiliary, and rename-temporary path
  families used by LCBetterSaves 1.7.3.
- Added locked .NET helper builds, path-confinement tests, v81 code/asset
  evidence, and CI verification of the embedded helper DLL.
- Added the Flet desktop UI for profile settings, workstation validation,
  smoke-test execution, cancellation, status, and artifact discovery.
- Added disposable BepInEx profile preparation, Debug DLL installation,
  Doorstop launch arguments, ready-marker detection, display capture, and
  structured run artifacts.
- Added atomic non-secret settings persistence and collision-safe request IDs.
- Added strict Ruff, mypy, and 100% statement-and-branch coverage gates.
- Added a least-privilege, SHA-pinned Windows GitHub Actions quality workflow.

### Security

- Restricted Thunderstore redirects to its HTTPS domain, bounded archive and
  transfer resources, and rejected traversal, links, digest mismatches, stale
  signatures, replayed nonces, and unapproved controllers.
- Required the save redirector ready marker before an instance becomes active;
  missing or failed isolation terminates the game and restores transactions.
- Restricted jobs and launch arguments to built-in values; no arbitrary shell
  or URL input is accepted.
- Added artifact path containment checks, environment-variable filtering,
  symbolic-link rejection for settings, and recoverable Doorstop restoration.
- Guarded the HTTP browser surface with exact Host and same-origin WebSocket
  checks, an eight-digit single-use code, five-guess limit, and local approval;
  browser approval is not written to the durable automation-key store.

### Notes

- The controller requires only a browser; the Agent workstation owns Python,
  profiles, package materialization, game processes, and recovery state.
- Browser traffic is unencrypted and requires a trusted private LAN plus a
  source-restricted firewall rule; the signed automation API remains pinned TLS.
- Live-game verification of the v81 Harmony save patch remains a pre-release
  validation task; static evidence and fail-closed startup are implemented.
- Full-display screenshots can contain notifications or unrelated windows;
  the workstation must be prepared before a run.
- Windows packaging, signing, and a stable release channel are not configured.
