# Changelog

All notable development changes to ModDebugPilot are recorded here. This file
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- Added the Flet desktop UI for profile settings, workstation validation,
  smoke-test execution, cancellation, status, and artifact discovery.
- Added disposable BepInEx profile preparation, Debug DLL installation,
  Doorstop launch arguments, ready-marker detection, display capture, and
  structured run artifacts.
- Added atomic non-secret settings persistence and collision-safe request IDs.
- Added strict Ruff, mypy, and 100% statement-and-branch coverage gates.
- Added a least-privilege, SHA-pinned Windows GitHub Actions quality workflow.

### Security

- Restricted jobs and launch arguments to built-in values; no arbitrary shell
  or URL input is accepted.
- Added artifact path containment checks, environment-variable filtering,
  symbolic-link rejection for settings, and recoverable Doorstop restoration.

### Notes

- Full-display screenshots can contain notifications or unrelated windows;
  the workstation must be prepared before a run.
- Windows packaging, signing, remote transport, multi-instance tests, and a
  stable release channel are not configured.
