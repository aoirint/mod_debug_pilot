# Target platform

## Verified scope

The source and semantic UI tests are verified on Windows 11 x64 with CPython
3.12.13 and Flet 0.85.3. The pinned SaveRedirect 0.1.0 dependency targets
BepInEx 5 and Lethal Company v81; its source repository owns the .NET and game
library build baseline.

Flet supplies the native Agent and Agent-hosted Web UI. Pillow's `ImageGrab`
captures the primary display. The Agent downloads exact packages selected by a
Thunderstore Profile Code, but never downloads the game itself.

## Integration constraints

- The game must run in the interactive console session.
- Resolution, DPI, game build, base profile, and mod build should remain fixed.
- Imported packages must produce Doorstop and the BepInEx preloader.
- The save redirector ready marker must appear in `BepInEx/LogOutput.log`.
- The primary display must remain available; an EDID emulator may be required
  on a headless test workstation.
- RDP connection changes can alter display, focus, capture, and GPU behavior.

## Evidence

The Python and Flet targets are declared in `pyproject.toml`, `.python-version`,
and `uv.lock`. `save_redirect.lock.json` records the independent plugin source
commit, version, runtime contract, and DLL digest. The BepInEx file contract and
Unity launch arguments are enforced by the runtime adapters and exercised with
offline tests.

Update this document when the Python/Flet range, OS target, capture method,
BepInEx generation, or Unity launch contract changes.
