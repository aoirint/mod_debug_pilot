# Remote security

## Trust establishment

The browser controller is deliberately served over HTTP. This avoids local
certificate installation and tunnel dependencies, but provides no
confidentiality or on-path integrity for DLL uploads, configs, screenshots, or
session traffic. It is supported only on a trusted private LAN with TCP 48951
restricted to the intended controller source.

The ASGI boundary accepts only discovered Agent hostnames and addresses plus
same-origin WebSockets. Pairing must be opened in the native window. Its
eight-digit code is single-use, expires after ten minutes, and closes after
five wrong guesses. A browser request remains unusable until the local operator
approves its displayed name.

Authorization stays in the server-side Flet page session and is not persisted.
Restarting the listener therefore requires a new pairing and approval.

## Authorized surface

The browser can import a profile, upload one bounded DLL, edit recognized config
files, install a verified bundle, list or launch instances, stop a tracked
instance, and download a bounded screenshot. It cannot submit arbitrary shell,
PowerShell, URL, registry, firewall, credential, environment, or launch-argument
operations.

Thunderstore is a separate fixed-trust boundary: redirects must remain on
HTTPS `thunderstore.io` hosts. Response bytes, ZIP entries, expanded size,
uploads, configs, and screenshots are bounded.

## Residual risks

- A device able to observe or alter the LAN path can read or tamper with the
  HTTP session. Use a separately administered trusted reverse proxy or a more
  isolated network when this is unacceptable.
- Binding to all interfaces requires a source-restricted Windows Firewall rule.
- A compromised Agent user can read artifacts or control its interactive game
  session.
- Full-display screenshots can reveal unrelated apps and notifications.
- Installed Thunderstore DLLs run with the test user's authority.
- SaveRedirect is statically verified for v81; other builds require a live-game
  smoke test.

Update this document after listener, pairing, authorization, download,
permission, or secret-handling changes.
