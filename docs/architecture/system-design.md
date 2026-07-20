# System design

This design depends on the [target platform](../domain/target-platform.md),
[dependency baseline](../domain/dependency-baseline.md), and
[SaveRedirect's Lethal Company v81 save evidence](https://github.com/aoirint/SaveRedirect/blob/main/docs/domain/lethal-company-v81-save-paths.md).

## Runtime topology

```text
Controller browser                         Windows test workstation
┌─────────────────────┐   trusted-LAN HTTP┌──────────────────────────────┐
│ Flet Web session    │◄─────────────────►│ Native ModDebugPilot Agent   │
│ Profile Code + DLL  │                   │ ├─ Flet Web host             │
│ Config editor       │                   │ ├─ pairing approval          │
│ Instance controls   │                   │ ├─ profile/runtime services  │
└─────────────────────┘                   │ └─ signed automation API     │
                                          │          │ exact operations     │
                                          │          ▼                    │
                                          │ Lethal Company instances     │
                                          │ + BepInEx save redirector    │
                                          └──────────────────────────────┘
```

The controller has no Python process, local profile directory, key file, or
package cache. The browser uploads the selected DLL into its Flet session.
Server-side controller handlers share Agent-owned services; they do not give
browser JavaScript filesystem or process access. Browser traffic is not
encrypted, so the HTTP route is permitted only inside a trusted,
firewall-restricted private LAN.

The separate API on port 48950 is reserved for approved Ed25519 controller
identities. Both surfaces expose structured operations only. The native Agent
owns listener start/stop, pairing decisions, recovery, and task termination.
The API retains pinned TLS and signed requests; the Web session instead relies
on its restricted LAN path, exact Host/Origin validation, single-use pairing,
and local approval.

## Profile workflow

1. Resolve a Profile Code through Thunderstore's legacy-profile endpoint.
2. Require the `#r2modman` prefix and decode the bounded Base64 `.r2z` ZIP.
3. Parse `export.r2x` with safe YAML loading and preserve imported `config/` files.
4. Download exact enabled package versions only from Thunderstore HTTPS hosts.
5. Extract recognized BepInEx, Doorstop, and root-plugin DLL layouts without
   links, traversal, unbounded expansion, or executable package scripts.
6. Add the uploaded local DLL and the pinned SaveRedirect artifact to dedicated
   plugin directories after verifying its provenance lock and SHA-256 digest.
7. Permit edits only to existing bounded `.cfg`, `.ini`, and `.json` files.
8. Create a profile ZIP whose manifest fixes every relative path, size, and
   SHA-256 digest. Revalidate it before installing an immutable profile.

Unknown Thunderstore installation layouts are ignored and an incomplete
BepInEx bootstrap is rejected. This first release is intentionally compatible
with the common Lethal Company package layouts, not every possible Thunderstore
install rule.

## Instance and save lifecycle

The Agent tracks the exact process handle it launched. Each instance receives a
fixed windowed resolution, unique Mono debugger port, copied profile, artifact
directory, and `SAVE_REDIRECT_ROOT`. The independently maintained SaveRedirect
BepInEx plugin patches the ES3 `FullPath` getter for file saves rooted at
`PersistentDataPath` and confines the result below that instance root.

The launch becomes `running` only after the plugin logs its ready marker.
Early exit or a 30-second timeout terminates the process and rolls back the
transaction. Multiple simultaneous profiles must use byte-identical
`winhttp.dll` and `doorstop_config.ini` because those files are shared in the
game directory.

Normal saves receive a second, journaled defense-in-depth boundary:

1. Refuse to start if an untracked Lethal Company process is active.
2. Journal whether the normal save directory existed and move it aside.
3. Create an empty fallback directory before the first debug instance.
4. Archive fallback debug files and restore the normal directory after the last
   live instance.
5. Recover both save and Doorstop journals before opening the API after an
   interrupted Agent process.

Ambiguous state—missing normal backup, foreign backup, archive collision, or
invalid journal—stops recovery without overwriting data.

## Legacy local workflow

The original single-machine `PilotView` and `LocalJobExecutor` remain available
for compatibility. Their domain/presentation/application dependency direction
is unchanged. New remote effects live behind explicit infrastructure services;
no remote module adds an arbitrary command boundary.

Update this document when profile formats, state ownership, allowed operations,
launch arguments, save behavior, artifacts, or cleanup changes.
