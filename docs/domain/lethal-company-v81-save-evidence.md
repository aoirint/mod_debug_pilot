# Lethal Company v81 save evidence

## Scope and result

This evidence applies only to the supplied Steam Lethal Company v81 build
identified by the local decompilation and asset export names ending in
`v81_1966720_22825947_6423525044216269478`. It establishes that ordinary save
names are passed to Easy Save 3 and resolve through Unity's
`Application.persistentDataPath`. It does not claim runtime verification of the
Harmony patch inside a live game process.

Evidence classification:

- **Direct static:** decompiled C# explicitly calls the save API or computes the
  path.
- **Effective static:** serialized project/default assets select the relevant
  default location and directory.
- **Runtime:** not yet observed for this exact helper; the Agent therefore waits
  for a plugin-emitted ready marker and fails closed.

## Managed-code evidence

| File | SHA-256 | Locator and finding |
| --- | --- | --- |
| `GameNetworkManager.cs` | `7AF7DEAFAB2EA2511F3EAA9AEFFFEC212DF3D4E4D01B219C9FAC1DD7D3AC787D` | Search `LCGeneralSaveData`, `LCSaveFile1`, and `ES3.`. The game selects those filenames and performs ES3 save/delete operations. |
| `DeleteFileButton.cs` | `5F9DF31AC28E9CDC53496D2C14C9F1361BB8742D36ED93666792449CB03279FD` | Search `ES3.DeleteFile`. Save-slot deletion uses the selected game save filename. |
| `SaveFileUISlot.cs` | `384CCBEF5990806CE6FF5C74CDDED9DFBB83322ED637F3553E03A07DBFFBECD8` | Search `fileName` and ES3 access. The UI associates slots with the same save-file names. |
| `ES3Settings.cs` | `44EEC6688186CCDA7EA52D05C256479E33BC312E66E4C800B6A09AA76D7FA4B8` | Inspect the `FullPath` getter. Persistent-data directory selection combines the requested path with `ES3IO.persistentDataPath`. |
| `ES3Internal/ES3IO.cs` | `13F11B4C28EFABB2C9F9298471AE0740E7B6C9D6BA3748856E54543273646609` | Search `persistentDataPath`. The static value is initialized from `Application.persistentDataPath`. |

The first three files are from the decompiled game managed-code export. The ES3
files are from the asset-source export. Paths are recorded relative to those
evidence roots so this report remains portable.

## Serialized-asset evidence

- `ProjectSettings/ProjectSettings.asset` sets `companyName` to
  `ZeekerssRBLX` and `productName` to `Lethal Company`.
- `Resources/es3/ES3Defaults.asset` sets `_location: 0`, `path: SaveFile.es3`,
  and `directory: 0`, corresponding to file storage in PersistentDataPath for
  the exported ES3 settings.

Together with the managed getter, those values establish the effective static
default. Unity documents `Application.persistentDataPath` as the persistent
per-application data directory. Unity's supported Player command-line argument
list does not provide a verified per-process override for that property, so the
design does not depend on an undocumented command-line switch.

References:

- [Unity `Application.persistentDataPath`](https://docs.unity3d.com/ja/2020.3/ScriptReference/Application-persistentDataPath.html)
- [Unity Player command-line arguments](https://docs.unity3d.com/ja/current/Manual/PlayerCommandLineArguments.html)

## Integration consequence

`ModDebugPilot.SaveRedirector` patches the `ES3Settings.FullPath` getter. It
redirects only `ES3.Location.File` plus
`ES3.Directory.PersistentDataPath`; all other ES3 locations continue through
the original getter. The requested filename must be relative and its canonical
path must remain under the absolute `MODDEBUGPILOT_SAVE_ROOT` supplied by the
Agent.

Before supporting a different game build, repeat the managed-code and asset
checks, record new hashes, run the helper boundary tests, and perform a live
smoke test that verifies the ready marker plus files created only under the
instance root.
