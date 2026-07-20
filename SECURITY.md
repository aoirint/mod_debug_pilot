# Security policy

## Reporting a vulnerability

Please use GitHub private vulnerability reporting for this repository. Do not
open a public issue containing credentials, machine paths, exploit details, or
Steam account information.

## Product boundary

ModDebugPilot is designed to execute only named, built-in jobs. Requests for an
arbitrary shell command, arbitrary download URL, credential extraction, or
firewall/antivirus disabling are outside the supported boundary.

Local configuration must not contain Steam passwords, SSH private keys, API
tokens, or other secrets. Test machines should use a dedicated, non-admin user
on an isolated LAN segment.

The Agent listeners are operator-started. The browser controller intentionally
uses plain HTTP; its Host/Origin checks and local pairing approval are
authorization boundaries, not encryption or protection against an on-path LAN
attacker. Restrict port 48951 to the exact controller address with Windows
Firewall or a dedicated test VLAN. Pairing approval does not make an untrusted
LAN safe. Do not expose either listener to the public Internet.

Automation requests require approved Ed25519 keys, fresh timestamps, unique
nonces, body-bound signatures, and a pinned self-signed TLS certificate. The
Agent-hosted Web controller uses an approved, non-persistent server-side page
session. Neither surface exposes arbitrary commands.

Thunderstore mods and the uploaded local DLL execute in the game process and
must be treated as code with the test user's authority. Profile validation
prevents archive and transport abuse; it does not prove a plugin is benign.

Normal-save and Doorstop journals fail closed on ambiguous recovery. Preserve
both candidate save directories and the Agent data root before manual repair;
do not delete a backup merely to make startup continue.

Screenshots contain the complete primary display. Disable notifications and
close unrelated applications before a test. ModDebugPilot filters the inherited
game-process environment, but artifact paths and logs can still reveal local
project names; review artifacts before sharing them.
