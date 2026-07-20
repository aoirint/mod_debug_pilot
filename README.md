# ModDebugPilot

ModDebugPilot is a Windows desktop tool for reproducible game-mod debugging.
It prepares disposable BepInEx profiles, launches an allow-listed test job in
the logged-in desktop session, and collects screenshots and logs as artifacts.

The first implementation milestone is under construction. The supported target
is Windows 11 with Python 3.12; Windows installer distribution is not yet
configured.

## Safety model

ModDebugPilot accepts named jobs and validated paths. It does not expose an
arbitrary PowerShell or command prompt. Use it with a dedicated non-admin test
account and keep Steam credentials outside its configuration.

## Development

Install [uv](https://docs.astral.sh/uv/) and run:

```powershell
uv sync --locked --all-groups
uv run --locked moddebugpilot
```

See the [developer documentation](docs/README.md) for architecture, external
contracts, and repeatable verification procedures.

## License

[MIT](LICENSE)
