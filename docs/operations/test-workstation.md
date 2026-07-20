# Test workstation

## Prerequisites

- A dedicated non-admin Windows user logged into the physical console session
- Steam and Lethal Company v81 already installed by the operator
- Fixed resolution, DPI, game version, and graphics configuration
- A monitor or EDID emulator and disabled sleep/lock behavior
- A restricted LAN or firewall rules permitting only the controller source

The Agent starts its listener only after a native-window action. Keep RDP for
maintenance because connection changes can alter display, focus, capture, GPU,
and controller behavior.

Allow TCP 48951 only from the browser controller's LAN address. The Flet Web
connection is HTTP and therefore unencrypted. Do not forward it through the
router or expose it to a guest or shared network.

## Filesystem boundaries

- The game directory is modified only for journaled `winhttp.dll` and
  `doorstop_config.ini` installation.
- Normal Lethal Company saves are moved to the adjacent
  `.moddebugpilot-normal` directory while any debug instance is active.
- Each debug instance receives its own directory under
  `<data-root>/instance-saves`.
- Installed profiles, journals, browser drafts, package results, and fallback
  debug-save archives live under the Agent data root.
- Screenshots, copied profiles, instance metadata, and collected game logs live
  under the artifact root.

Do not put the Agent data or artifact root inside the game directory. Do not
store Steam credentials or tokens in Agent settings.
Disable notifications because screenshots capture the complete primary display.

Use [pairing and recovery](pairing-and-recovery.md) for normal operation and
failure handling.
