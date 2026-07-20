"""Atomic non-secret JSON settings persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path


def application_data_dir(
    *,
    environment: Mapping[str, str],
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


def write_json_atomic(*, path: Path, payload: Mapping[str, object]) -> None:
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
