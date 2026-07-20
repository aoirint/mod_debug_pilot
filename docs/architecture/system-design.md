# System design

This design depends on the [target platform](../domain/target-platform.md), the
[dependency baseline](../domain/dependency-baseline.md), and
[SaveRedirect's v81 evidence](https://github.com/aoirint/SaveRedirect/blob/main/docs/domain/lethal-company-v81-save-paths.md).

## Runtime topology

```text
Controller browser                         Windows test workstation
┌─────────────────────┐   trusted-LAN HTTP ┌──────────────────────────────┐
│ Flet Web page       │◄──────────────────►│ Native ModDebugPilot Agent   │
│ Profile Code + DLL  │                    │ ├─ local pairing approval    │
│ Config editor       │                    │ ├─ Flet Web host             │
│ Instance controls   │                    │ └─ profile/runtime services  │
└─────────────────────┘                    │               │              │
                                           │               ▼              │
                                           │ Lethal Company instances     │
                                           │ + BepInEx SaveRedirect       │
                                           └──────────────────────────────┘
```

The browser has no Python process, profile directory, key file, or package
cache. It uploads the selected DLL into its server-owned Flet session. The
native Agent owns listener start and stop, local approval, recovery, and task
termination. There is one control plane: the Agent-hosted HTTP Flet Web app.

## Python boundaries

The source tree follows one dependency direction:

```text
domain <- application <- presentation <- ui
                  ^                 ^
                  └ infrastructure ─┴ composition
```

- `domain` owns immutable validation models and errors.
- `application` owns use cases and effect protocols without importing Flet.
- `presentation` owns immutable view state and Flet-free controllers.
- `infrastructure` implements network, filesystem, process, capture, and
  Thunderstore effects.
- `ui` maps Flet events and controls to presentation controllers.
- `composition` is the only concrete wiring boundary.
- `entrypoints` resolve application data and launch the composed native page.

UI modules do not construct infrastructure services. Browser sessions receive
an application context from composition and own only disposable draft state.

## Profile workflow

1. Resolve a Profile Code through Thunderstore's legacy-profile endpoint.
2. Require the `#r2modman` prefix and decode the bounded Base64 `.r2z` ZIP.
3. Parse `export.r2x` with safe YAML loading and preserve imported configs.
4. Download exact enabled package versions from Thunderstore HTTPS hosts.
5. Extract recognized BepInEx, Doorstop, and root-plugin layouts without links,
   traversal, unbounded expansion, or executable package scripts.
6. Add the uploaded local DLL and pinned SaveRedirect artifact after verifying
   provenance and SHA-256.
7. Permit edits only to existing bounded `.cfg`, `.ini`, and `.json` files.
8. Build and revalidate a manifest-and-digest profile bundle before install.

Unknown package layouts are ignored and an incomplete BepInEx bootstrap is
rejected. Version 0.1 intentionally supports common Lethal Company layouts,
not every Thunderstore install rule.

## Instance and save lifecycle

Each instance receives a fixed windowed resolution, unique debugger port,
copied profile, artifact directory, and `SAVE_REDIRECT_ROOT`. It becomes
`running` only after SaveRedirect logs its ready marker. Early exit or timeout
terminates the process and rolls back the transaction.

Normal saves receive a second journaled boundary:

1. Refuse the first launch if an untracked Lethal Company process is active.
2. Journal and move the normal save directory aside.
3. Create an empty fallback directory while debug instances are active.
4. Archive fallback files and restore normal saves after the last instance.
5. Recover save and Doorstop journals before reopening the controller.

Ambiguous state stops recovery without overwriting data. Update this document
when profile formats, dependency direction, listeners, save behavior, artifacts,
or cleanup change.
