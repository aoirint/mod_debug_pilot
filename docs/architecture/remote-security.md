# Remote security

## Trust establishment

The native Agent creates one self-signed RSA TLS identity. Its private key is
encrypted with an operator-entered passphrase that is never persisted. The
native window displays the SHA-256 certificate fingerprint and controller URL.
The operator must compare that fingerprint with the browser certificate before
accepting the self-signed certificate warning.

Pairing is explicitly opened in the native window. The six-digit code is
single-use and expires after ten minutes. A request records the controller name,
public-key-derived ID, and a high-entropy polling token. Only the local native
window can approve or reject it.

The Agent-hosted Flet Web session uses an in-memory Ed25519 identity and keeps
authorization in that server-side page session. It writes no controller key to
the browser machine. The automation API persists approved public keys only.

## Automation API authentication

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

- A user who ignores the certificate fingerprint can approve a man-in-the-middle.
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
