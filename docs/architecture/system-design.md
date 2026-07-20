# System design

This design depends on the [target platform](../domain/target-platform.md) and
[dependency baseline](../domain/dependency-baseline.md).

## Dependency direction

```text
entrypoint -> composition -> ui -> presentation -> application -> domain
                              \                 /
                               infrastructure --
```

Domain values and application services do not import Flet or concrete file and
process adapters. Presentation owns immutable `AppState` snapshots and one
active asynchronous task. Flet controls render snapshots and emit typed intents.
Infrastructure owns settings, profile copies, process lifetime, screen capture,
and artifacts.

## UI state and task ownership

`AppController` owns the mutually exclusive loading, ready, saving, running,
succeeded, failed, canceled, and closed phases. One generation number rejects
stale completion. Duplicate starts and saves while busy are rejected.

The page session owns the controller. Close and disconnect handlers unsubscribe
rendering before closing the controller. Controller close cancels and awaits its
active task and is idempotent.

## Runner workflow

1. Validate the Windows platform and required local files.
2. Write `request.json` and a redacted `environment.json`.
3. Copy the base profile to `<artifact-dir>/profile`.
4. Copy the Debug DLL to `BepInEx/plugins/ModDebugPilot`.
5. Back up at most `winhttp.dll` and `doorstop_config.ini` in the game directory.
6. Install the run profile's Doorstop files.
7. Launch the game directly with a fixed argument vector and filtered environment.
8. Wait for early exit, the ready marker, cancellation, or timeout.
9. Capture the primary display after the configured delay.
10. Terminate the process tree, restore Doorstop files, collect the BepInEx log,
    and write `result.json`.

Cleanup nests Doorstop restoration inside process cleanup so a termination error
cannot skip restoration. A pre-existing backup directory is treated as foreign
state and is never modified.

## Data and security contracts

Settings are public local data: four paths, profile name, timeout, resolution,
ready marker, screenshot delay, and debugger port. They are validated and
atomically replaced in the platform application-data directory. Symbolic-link
settings paths and files over 64 KiB are rejected.

Job identifiers are restricted to a safe character set. Artifact roots inside
the game or base-profile tree are rejected. The game inherits only a reviewed
set of ordinary Windows environment variables plus a generated
`MONO_ENV_OPTIONS`; arbitrary environment entries are not forwarded.

The application stores no credentials and performs no network request. Its
remaining sensitive outputs are full-display screenshots, local path names,
game logs, and copied profiles.

Update this document when state ownership, dependency direction, allowed jobs,
launch arguments, persistence, artifacts, or cleanup changes.
