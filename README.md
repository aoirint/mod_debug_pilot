# ModDebugPilot

ModDebugPilot is a Windows desktop tool for reproducible game-mod debugging.
It creates a disposable BepInEx profile for each run, launches the game in the
logged-in desktop session, waits for a known log marker, captures the primary
display, and keeps the evidence in one artifact directory.

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB)
![Flet 0.85.3](https://img.shields.io/badge/Flet-0.85.3-02569B)

## Current capabilities

- Save one local test-profile configuration.
- Validate the game, base BepInEx profile, Debug DLL, and Windows environment.
- Copy the base profile into a run-specific artifact directory.
- Install the selected Debug DLL under `BepInEx/plugins/ModDebugPilot`.
- Launch the game with fixed resolution, Doorstop target, and Mono debugger port.
- Detect a configurable BepInEx log marker, then capture the primary display.
- Distinguish success, validation failure, early exit, timeout, and cancellation.
- Restore pre-existing `winhttp.dll` and `doorstop_config.ini` files after a run.

ModDebugPilot does not download BepInEx, mods, or game files. It does not expose
an arbitrary PowerShell, shell command, launch argument, or URL field.

## Requirements

- Windows 11 x64
- A dedicated non-admin Windows user logged into the console session
- Steam and the target Unity game installed by the operator
- A prepared BepInEx 5 Mono base profile containing:
  - `winhttp.dll`
  - `doorstop_config.ini`
  - `BepInEx/core/BepInEx.Preloader.dll`
- A locally built mod DLL
- [uv](https://docs.astral.sh/uv/) for source-based use

## Run from source

```powershell
uv sync --locked --all-groups
uv run --locked moddebugpilot
```

The application stores non-secret settings under Flet's application-data
directory. On an unpackaged Windows run, it falls back to
`%APPDATA%\ModDebugPilot\settings.json`.

## Configure a profile

1. Select the game executable, BepInEx base-profile directory, Debug mod DLL,
   and artifact root.
2. Keep the artifact root outside the game and base-profile directories.
3. Set the log marker emitted after BepInEx and the target mod are ready.
4. Save settings, then run **Validate workstation**.
5. Run **Run smoke test** only after validation passes.

The default resolution is 1280×720, the timeout is 180 seconds, and the Mono
debugger port is 55555. A smoke test stops the game after the ready screenshot.

## Artifacts

Each job writes `<artifact-root>/<job-id>/`:

```text
request.json
environment.json
result.json
game.log                 # when BepInEx created it
screenshots/ready.png    # after the ready marker
profile/                 # disposable run profile
```

The screenshot captures the entire primary display. Disable notifications and
close unrelated applications on the test workstation before running a test.

## Safety and recovery

- Use a dedicated test account and isolated LAN segment.
- Do not put Steam credentials, SSH keys, or tokens in settings.
- Keep RDP for maintenance; disconnecting RDP can change the graphics session.
- If process cleanup or Doorstop restoration fails, stop further jobs and
  inspect `result.json` plus the `.moddebugpilot-backup-<job-id>` directory in
  the game directory before changing files manually.

See the [developer documentation](docs/README.md) for architecture, dependency
evidence, CI, workstation recovery, and verification procedures.

## Status and license

The project is pre-release (`0.1.0.dev0`). Source, wheel, and sdist builds are
verified; a signed Windows installer and release channel are not configured.

[MIT](LICENSE)
