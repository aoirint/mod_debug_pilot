# Remote security

## Browser trust establishment

The browser controller is deliberately served over HTTP. This removes browser
certificate installation and tunnel dependencies, but provides no
confidentiality or on-path integrity for DLL uploads, configuration,
screenshots, or session traffic. It is supported only on a trusted private LAN
with port 48951 restricted to the intended controller source.

The ASGI boundary accepts only exact discovered Agent hostnames/IP addresses and
same-origin HTTP WebSockets, reducing DNS-rebinding and cross-site initiation.
Pairing is explicitly opened in the native window. The eight-digit code is
single-use, expires after ten minutes, and closes after five wrong guesses. A
request records the controller name, public-key-derived ID, and a high-entropy
polling token. Only the local native window can approve or reject it.

The Agent-hosted Flet Web session uses an in-memory Ed25519 identity and keeps
authorization in that server-side page session. It writes no controller key to
the browser machine or durable Agent authorization store. The automation API
persists approved public keys only.

## Automation API authentication

The Agent creates one self-signed RSA TLS identity for the API. Its private key
is encrypted with an operator-entered passphrase that is never persisted. API
clients pin the certificate bytes; the fingerprint shown in the native window
belongs to this automation surface, not the HTTP browser controller.

Every protected request binds these fields into an Ed25519 signature:

- protocol marker;
- uppercase HTTP method;
- exact URL path without query parameters;
- SHA-256 request-body digest;
- timestamp; and
- high-entropy nonce.

The Agent rejects unknown keys, signatures outside the freshness window, reused
nonces, malformed encodings, and modified method/path/body data. The client pins
the Agent certificate bytes and never falls back to public-CA trust. Redirects
are disabled.

## Authorized surface

The API permits only identity discovery, pairing, profile install, instance
list/launch/stop, screenshot creation, and bounded artifact download. It has no
arbitrary shell, PowerShell, URL download, registry, firewall, credential,
environment, or free-form launch-argument operation.

Thunderstore downloads are a separate fixed-trust boundary: only HTTPS
`thunderstore.io` and its subdomains are accepted at every redirect. Package and
profile bytes, ZIP entry count, expanded size, upload size, configuration size,
and artifact size are bounded.

## Residual risks

- Any device able to observe or alter the trusted LAN path can read or tamper
  with the HTTP browser session; use the pinned API when that risk is unacceptable.
- The default bind address exposes both listeners on all interfaces; restrict
  access with network segmentation and Windows Firewall.
- A locally compromised Agent user can read artifacts, modify the application,
  or control its interactive game session.
- Full-desktop screenshots can reveal unrelated applications and notifications.
- Thunderstore package bytes are treated as data, but installed plugin DLLs run
  inside the game and therefore inherit the test user's authority.
- The helper's Harmony integration is statically verified for v81; a real-game
  runtime smoke test remains required before claiming compatibility with another
  Lethal Company build.

Update this document after any listener, identity, pairing, signature, download,
authorization, permission, or secret-handling change.
