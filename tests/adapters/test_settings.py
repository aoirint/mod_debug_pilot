"""Tests for platform paths and atomic settings persistence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mod_debug_pilot.domain import PilotConfig, ValidationError
from mod_debug_pilot.infrastructure.settings import (
    JsonConfigRepository,
    UnsafeSettingsPathError,
    application_data_dir,
    write_json_atomic,
)
from tests.unit.test_domain import valid_values


def test_application_data_dir_prefers_packaged_then_windows_paths(*, tmp_path: Path) -> None:
    """Packaged storage wins, with APPDATA and home fallbacks."""
    assert (
        application_data_dir(
            environment={"FLET_APP_STORAGE_DATA": str(tmp_path / "packaged"), "APPDATA": "ignored"},
            home=tmp_path,
        )
        == tmp_path / "packaged"
    )
    assert application_data_dir(environment={"APPDATA": str(tmp_path)}, home=Path("ignored")) == (
        tmp_path / "ModDebugPilot"
    )
    assert application_data_dir(environment={}, home=tmp_path) == (
        tmp_path / "AppData" / "Roaming" / "ModDebugPilot"
    )


def test_repository_round_trip_and_missing_file(*, tmp_path: Path) -> None:
    """Settings save atomically and missing settings mean first use."""
    repository = JsonConfigRepository(path=tmp_path / "nested" / "settings.json")
    config = PilotConfig.from_mapping(values=valid_values())

    assert asyncio.run(repository.load()) is None
    asyncio.run(repository.save(config=config))

    assert asyncio.run(repository.load()) == config
    payload = json.loads((tmp_path / "nested" / "settings.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff", "not valid JSON"),
        (b"{", "not valid JSON"),
        (b"[]", "schema is not supported"),
        (b'{"schema_version": 2}', "schema is not supported"),
        (b'{"schema_version": 1}', "no config object"),
    ],
)
def test_repository_rejects_corrupt_schema(
    *,
    tmp_path: Path,
    content: bytes,
    message: str,
) -> None:
    """Corrupt, unsupported, and incomplete JSON is rejected."""
    path = tmp_path / "settings.json"
    path.write_bytes(content)

    with pytest.raises(ValidationError, match="Configuration is invalid") as raised:
        asyncio.run(JsonConfigRepository(path=path).load())

    assert message in raised.value.errors["file"]


def test_repository_rejects_large_and_symbolic_settings(*, tmp_path: Path) -> None:
    """Size and symlink boundaries are enforced before parsing."""
    path = tmp_path / "settings.json"
    path.write_bytes(b"x" * (64 * 1024 + 1))

    with pytest.raises(ValidationError) as large:
        asyncio.run(JsonConfigRepository(path=path).load())
    assert large.value.errors == {"file": "Saved configuration is too large."}

    path.write_text("{}", encoding="utf-8")
    with (
        patch.object(Path, "is_symlink", return_value=True),
        pytest.raises(
            UnsafeSettingsPathError,
        ),
    ):
        asyncio.run(JsonConfigRepository(path=path).load())


def test_atomic_writer_removes_temporary_file_after_failure(*, tmp_path: Path) -> None:
    """A serialization failure leaves no partial settings file."""
    path = tmp_path / "settings.json"

    with (
        patch("mod_debug_pilot.infrastructure.settings.json.dump", side_effect=TypeError),
        pytest.raises(TypeError),
    ):
        write_json_atomic(path=path, payload={"bad": object()})

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
