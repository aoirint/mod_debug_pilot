# ModDebugPilot

ModDebugPilot is a Windows test-workstation Agent for Lethal Company mod
development. The native Agent starts an HTTPS-hosted Flet Web controller only
when the local operator asks it to, displays the certificate fingerprint and a
one-time pairing code, and requires local approval before a browser session can
prepare profiles or control game instances.

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB)
![Flet 0.85.3](https://img.shields.io/badge/Flet-0.85.3-02569B)

## Capabilities

- Import a Thunderstore App/r2modman Profile Code.
- Download only exact enabled Thunderstore package versions over bounded HTTPS.
- Add a locally selected Debug mod DLL and edit imported UTF-8 mod configs.
- Install a manifest-and-SHA-256-verified profile on the Agent.
- Launch, list, screenshot, and stop multiple tracked Lethal Company instances.
- Give every instance a distinct injected ES3 save root through a bundled
  BepInEx 5 helper plugin.
- Journal and restore normal saves plus the shared Doorstop bootstrap after the
  final instance, shutdown, startup recovery, or failed launch.
- Expose a signed, certificate-pinned automation API without an arbitrary shell,
  URL, environment-variable, or launch-argument operation.

The browser computer needs only a current browser. Profile assembly and mod
selection are initiated from the Web UI, while package materialization, runtime
state, and game processes remain owned by the Agent workstation.

## Requirements

- Windows 11 x64 on the Agent workstation
- A dedicated non-admin user logged into the physical console session
- Steam and Lethal Company v81 installed by the operator
- Network isolation or a host firewall restricting ports 48950 and 48951 to the
  intended LAN controller
- Python 3.12, [.NET SDK 10.0.201](global.json), and
  [uv](https://docs.astral.sh/uv/) for source development

## Start the Agent

```powershell
uv sync --locked --all-groups
uv run --locked moddebugpilot-agent
```

In the native window:

1. Set the game executable, Agent data, artifacts, and normal-save directory.
2. Enter a strong passphrase. It encrypts the Agent TLS private key and is not
   persisted.
3. Select **Start secure listeners**.
4. Open the displayed Controller URL in the controller browser and compare the
   SHA-256 certificate fingerprint with the native Agent window.
5. Select **Open pairing window**, enter its one-time code in the browser, then
   approve the named request in the native Agent window.

The certificate is self-signed, so the initial browser warning is expected.
Do not continue if the browser certificate fingerprint does not match the Agent.

The `moddebugpilot` and `moddebugpilot-agent` entry points currently launch the
same native Agent application.

## Safety boundary

The Agent accepts structured profile, instance, screenshot, and artifact
operations only. It does not accept arbitrary commands. Profile ZIP paths,
package redirects, response sizes, file counts, upload sizes, artifact paths,
identifiers, signatures, timestamps, and nonces are validated before effects.

Save isolation is fail-closed: a game process must emit
`[MODDEBUGPILOT] save_redirect_ready` from the bundled BepInEx plugin within 30
seconds or the Agent terminates it. Normal saves are also moved under a journaled
outer transaction as defense in depth and are restored automatically after the
last instance.

Full-display screenshots and logs can contain private information. Disable
notifications and review artifacts before sharing them.

See the [developer documentation](docs/README.md) for the protocol, v81 save
evidence, dependency provenance, workstation recovery, and verification steps.

## Status and license

The project is pre-release (`0.1.0.dev0`). The application, helper plugin, wheel,
and sdist are verified locally; a signed Windows installer and release channel
are not configured.

[MIT](LICENSE)
