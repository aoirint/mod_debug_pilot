# Target platform

## Scope

The initial target is a Windows 11 x64 interactive desktop session, Python
3.12, Flet 0.85.x or 0.86.x subject to the seven-day dependency cooldown, and
BepInEx 5 Mono profiles supplied by the user.

Flet provides the desktop UI and exposes packaged application storage through
`FLET_APP_STORAGE_DATA`. Pillow provides primary-display capture. BepInEx and
game binaries are never downloaded by the application.

## Integration constraints

- A game requiring graphics must run in the logged-in console session.
- The display resolution and DPI should remain fixed during image-based tests.
- A prepared BepInEx base profile must contain the expected Doorstop and
  `BepInEx` files before a run starts.
- ModDebugPilot may copy user-selected local files but does not accept remote
  archive URLs.

Update this document when the supported Python/Flet range, OS, capture method,
or BepInEx generation changes.
