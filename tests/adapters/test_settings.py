"""Tests for platform paths and atomic JSON persistence."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mod_debug_pilot.infrastructure.settings import application_data_dir, write_json_atomic


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


def test_atomic_writer_round_trip_and_failure_cleanup(*, tmp_path: Path) -> None:
    """The writer replaces valid JSON and removes a failed temporary file."""
    path = tmp_path / "nested" / "settings.json"
    write_json_atomic(path=path, payload={"schema_version": 1, "value": "ok"})
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == "ok"

    failed = tmp_path / "failed" / "settings.json"
    with (
        patch("mod_debug_pilot.infrastructure.settings.json.dump", side_effect=TypeError),
        pytest.raises(TypeError),
    ):
        write_json_atomic(path=failed, payload={"bad": object()})

    assert not failed.exists()
    assert list(failed.parent.iterdir()) == []
