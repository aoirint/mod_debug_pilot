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
