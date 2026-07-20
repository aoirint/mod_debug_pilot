# Test workstation

## Prerequisites

- A dedicated non-admin Windows user logged into the console session
- Steam and the target game already installed by the operator
- A locally prepared BepInEx 5 Mono base profile
- Fixed resolution, DPI, game version, and mod build

## Safety and recovery

Run only one ModDebugPilot job at a time. The application writes inside its
configured artifact and disposable-run roots; source game and base-profile
directories are treated as read-only. If cleanup cannot terminate the game or
restore a managed bootstrap file, stop accepting jobs and inspect the recorded
result before retrying.

Do not store Steam credentials, SSH keys, or tokens in configuration. Use RDP
for maintenance only because disconnecting it can change the graphics session.

Update this runbook when workstation provisioning, process cleanup, or remote
transport behavior changes.
