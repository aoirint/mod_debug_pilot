# Test workstation

## Prerequisites

- A dedicated non-admin Windows user logged into the console session
- Steam and the target game already installed by the operator
- A locally prepared BepInEx 5 Mono base profile
- Fixed resolution, DPI, game version, and mod build

## Safety and recovery

Run only one ModDebugPilot job at a time. The application writes inside its
configured artifact and disposable-run roots; source game and base-profile
directories are treated as read-only except for temporary management of
`winhttp.dll` and `doorstop_config.ini` in the game directory.

If cleanup cannot terminate the game or restore a managed bootstrap file:

1. Stop accepting jobs and close ModDebugPilot.
2. Confirm the game process tree has stopped.
3. Inspect `result.json` and `.moddebugpilot-backup-<job-id>`.
4. Restore only files that belong to that job; never reuse or delete a foreign
   backup directory merely because its name is similar.
5. Re-run **Validate workstation** before another smoke test.

Do not store Steam credentials, SSH keys, or tokens in configuration. Use RDP
for maintenance only because disconnecting it can change the graphics session.
Disable notifications because a ready screenshot captures the primary display.

Update this runbook when workstation provisioning, process cleanup, or remote
transport behavior changes.
