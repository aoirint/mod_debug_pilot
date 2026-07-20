# Target platform

## Verified scope

The source and semantic UI tests are verified on Windows 11 x64 with CPython
3.12.13, Flet 0.85.3, and Pillow 12.3.0. The product target is a logged-in
Windows 11 desktop session running a Unity game through a user-supplied BepInEx
5 Mono profile.

Flet supplies the desktop UI and packaged application-data path. Pillow's
`ImageGrab` captures the primary display. BepInEx and game binaries are never
downloaded by the application.

## Integration constraints

- The game must run in the interactive console session.
- Resolution, DPI, game build, base profile, and mod build should remain fixed.
- The base profile must contain Doorstop and the BepInEx preloader before a run.
- The configured ready marker must appear in `BepInEx/LogOutput.log`.
- The primary display must remain available; an EDID emulator may be required
  on a headless test workstation.
- RDP connection changes can alter display, focus, capture, and GPU behavior.

## Evidence

The Python and Flet targets are declared in `pyproject.toml`, `.python-version`,
and `uv.lock`. The BepInEx file contract and Unity launch arguments are enforced
by `infrastructure/runner.py` and exercised with offline adapter tests.

Update this document when the Python/Flet range, OS target, capture method,
BepInEx generation, or Unity launch contract changes.
