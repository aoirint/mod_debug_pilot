"""Atomic non-secret JSON settings persistence."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from mod_debug_pilot.domain import PilotConfig, ValidationError

_MAX_SETTINGS_BYTES = 64 * 1024


class UnsafeSettingsPathError(OSError):
    """Reject a settings path redirected through a symbolic link."""


def application_data_dir(
    environment: Mapping[str, str],
    *,
    home: Path,
) -> Path:
    """Resolve the packaged or ordinary Windows application-data directory."""
    packaged = environment.get("FLET_APP_STORAGE_DATA", "").strip()
    if packaged:
        return Path(packaged)
    appdata = environment.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "ModDebugPilot"
    return home / "AppData" / "Roaming" / "ModDebugPilot"


class JsonConfigRepository:
    """Store one validated public configuration with atomic replacement."""

    def __init__(self, path: Path) -> None:
        """Create a repository for a specific settings file."""
        self._path = path

    async def load(self) -> PilotConfig | None:
        """Read and validate settings off the UI event loop."""
        return await asyncio.to_thread(self._load_sync)

    async def save(self, config: PilotConfig) -> None:
        """Write settings atomically off the UI event loop."""
        await asyncio.to_thread(
            write_json_atomic,
            self._path,
            payload={"schema_version": 1, "config": config.to_mapping()},
        )

    def _load_sync(self) -> PilotConfig | None:
        if not self._path.exists():
            return None
        if self._path.is_symlink():
            raise UnsafeSettingsPathError
        if self._path.stat().st_size > _MAX_SETTINGS_BYTES:
            raise ValidationError({"file": "Saved configuration is too large."})
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError({"file": "Saved configuration is not valid JSON."}) from error
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValidationError({"file": "Saved configuration schema is not supported."})
        values = payload.get("config")
        if not isinstance(values, dict):
            raise ValidationError({"file": "Saved configuration has no config object."})
        return PilotConfig.from_mapping(values)


def write_json_atomic(path: Path, *, payload: Mapping[str, object]) -> None:
    """Write UTF-8 JSON through a private same-filesystem temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            temporary.chmod(0o600)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
