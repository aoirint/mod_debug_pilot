# ModDebugPilot

ModDebugPilot is a Windows test-workstation Agent for Lethal Company mod
development. The native Flet application starts an HTTP Flet Web controller
only when the local operator asks it to. A browser session must present a
short-lived code and receive approval in the native window before it can
prepare profiles or control game instances.

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB)
![Flet 0.85.3](https://img.shields.io/badge/Flet-0.85.3-02569B)

## Capabilities

- Import a Thunderstore App/r2modman Profile Code.
- Download exact enabled package versions over bounded HTTPS.
- Add a browser-selected local Debug DLL and edit imported UTF-8 configs.
- Install a manifest-and-SHA-256-verified profile on the Agent.
- Launch, list, screenshot, and stop multiple tracked Lethal Company instances.
- Give each instance an isolated Easy Save 3 root through the pinned independent
  [SaveRedirect](https://github.com/aoirint/SaveRedirect) BepInEx plugin.
- Journal and restore normal saves and the shared Doorstop bootstrap after the
  final instance, shutdown, startup recovery, or failed launch.

The controller computer needs only a current browser. Python, package caches,
profiles, game processes, and recovery state remain on the Agent workstation.

## Requirements

- Windows 11 x64 on the Agent workstation
- A dedicated non-admin user logged into the physical console session
- Steam and Lethal Company v81 installed by the operator
- A trusted private LAN and a host firewall restricting TCP 48951 to the
  intended controller
- Python 3.12 and [uv](https://docs.astral.sh/uv/) for source development

## Start the Agent

```powershell
uv sync --locked --all-groups
uv run --locked moddebugpilot
```

In the native window:

1. Set the game executable, Agent data, artifacts, and normal-save directory.
   Each path accepts direct text entry and provides a native **Browse…** file or
   directory picker.
2. Select **Start controller**.
3. Open the displayed Controller URL from the trusted private LAN.
4. Select **Open pairing window**, submit its code in the browser, and approve
   the named request in the native Agent window.

The controller deliberately uses HTTP so the browser needs neither a locally
trusted certificate nor a public tunnel. HTTP does not protect DLLs, configs,
screenshots, or session traffic from another device able to observe or alter
the LAN. Do not expose the listener to the internet, a guest network, or an
untrusted shared network.

## Safety boundary

The Agent accepts only structured profile, instance, screenshot, and artifact
operations. It has no arbitrary shell, URL, environment-variable, or free-form
launch-argument operation. Profile paths, redirects, resource sizes, file
counts, uploads, artifacts, identifiers, and Web origins are validated before
effects.

Save isolation is fail-closed: a game process must emit `[SAVEREDIRECT] ready`
from the pinned SaveRedirect plugin within 30 seconds or the Agent terminates
it. Normal saves are moved under a journaled outer transaction as defense in
depth and restored automatically after the last instance.

The redirect boundary covers the Easy Save 3 file families used by
LCBetterSaves 1.7.3 (`LCSaveFileN`, `LGU_N.json`, and rename temporaries), so
additional slots remain inside the instance root. Static and boundary tests
cover Lethal Company v81; a live-game compatibility smoke test is still needed.

Full-display screenshots and logs can contain private information. Disable
notifications and review artifacts before sharing them.

See the [developer documentation](docs/README.md) for architecture, security,
dependency provenance, workstation recovery, and verification.

## Status and license

The project is pre-release (`0.1.0.dev0`). Windows packaging is verified in CI;
code signing and a stable release channel are not configured.

[MIT](LICENSE)
