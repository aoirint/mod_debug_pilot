"""Exhaustive validation tests for remote wire contracts."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from mod_debug_pilot.domain import (
    AgentSettings,
    BundleManifest,
    FileRecord,
    InstanceSnapshot,
    InstanceSpec,
    InstanceStatus,
    RemoteValidationError,
    normalize_fingerprint,
)

_DEFAULT_API_PORT = 48950
_DEFAULT_WEB_PORT = 48951


def agent_values() -> dict[str, object]:
    """Return one valid Agent form mapping."""
    return {
        "agent_name": "lab-agent",
        "bind_host": "127.0.0.1",
        "api_port": "48950",
        "web_port": 48951,
        "game_executable": r"C:\Game\Lethal Company.exe",
        "data_root": r"C:\MDP\data",
        "artifact_root": r"C:\MDP\artifacts",
        "save_directory": r"C:\Users\tester\AppData\LocalLow\ZeekerssRBLX\Lethal Company",
    }


def test_agent_settings_round_trip_and_defaults() -> None:
    """Form strings normalize to one stable non-secret JSON object."""
    settings = AgentSettings.from_mapping(value=agent_values())

    assert settings.agent_name == "lab-agent"
    assert settings.api_port == _DEFAULT_API_PORT
    assert settings.to_mapping()["schema_version"] == 1
    assert AgentSettings().web_port == _DEFAULT_WEB_PORT


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        {**agent_values(), "agent_name": ""},
        {**agent_values(), "agent_name": 3},
        {**agent_values(), "agent_name": "x" * 4097},
        {**agent_values(), "api_port": "bad"},
        {**agent_values(), "api_port": 80},
        {**agent_values(), "web_port": 48950},
    ],
)
def test_agent_settings_reject_invalid_values(*, mutation: object) -> None:
    """Every invalid field shape fails before effects are composed."""
    with pytest.raises(RemoteValidationError):
        AgentSettings.from_mapping(value=mutation)


def test_file_record_and_manifest_round_trip() -> None:
    """Bundle records preserve exact paths, sizes, and source dependencies."""
    record = FileRecord(path="BepInEx/plugins/mod.dll", size=3, sha256="a" * 64)
    manifest = BundleManifest(
        profile_name="smoke-1",
        created_at="2026-07-20T00:00:00+00:00",
        files=(record,),
        source_mods=("BepInEx-BepInExPack-5.4.2100",),
    )

    parsed = BundleManifest.from_mapping(value=manifest.to_mapping())

    assert parsed == manifest
    assert FileRecord.from_mapping(value=record.to_mapping()) == record


def test_file_record_rejections() -> None:
    """Unsafe paths, types, sizes, and digests are rejected."""
    invalid = [
        {"path": "", "size": 0, "sha256": "a" * 64},
        {"path": "/absolute", "size": 0, "sha256": "a" * 64},
        {"path": "../escape", "size": 0, "sha256": "a" * 64},
        {"path": "a\\b", "size": 0, "sha256": "a" * 64},
        {"path": "a", "size": -1, "sha256": "a" * 64},
        {"path": "a", "size": 0, "sha256": "bad"},
    ]
    for fields in invalid:
        with pytest.raises(RemoteValidationError):
            FileRecord(**fields)  # type: ignore[arg-type]
    for value in (None, {}, {"path": 1, "size": "0", "sha256": 2}):
        with pytest.raises(RemoteValidationError):
            FileRecord.from_mapping(value=value)


def test_manifest_rejections() -> None:
    """Schema, names, timestamps, duplicates, and wire types remain strict."""
    record = FileRecord(path="a", size=0, sha256="a" * 64)
    with pytest.raises(RemoteValidationError):
        BundleManifest(profile_name="bad/name", created_at="x", files=())
    with pytest.raises(RemoteValidationError):
        BundleManifest(profile_name="ok", created_at="", files=())
    with pytest.raises(RemoteValidationError):
        BundleManifest(profile_name="ok", created_at="x", files=(), schema_version=2)
    with pytest.raises(RemoteValidationError):
        BundleManifest(profile_name="ok", created_at="x", files=(record, record))
    invalid_values: tuple[object, ...] = (
        None,
        {},
        {"files": {}, "source_mods": []},
        {"files": [], "source_mods": [], "profile_name": 1, "created_at": 2, "schema_version": "1"},
        {
            "files": [],
            "source_mods": [1],
            "profile_name": "ok",
            "created_at": "x",
            "schema_version": 1,
        },
    )
    for value in invalid_values:
        with pytest.raises(RemoteValidationError):
            BundleManifest.from_mapping(value=value)


def test_instance_spec_and_snapshot_round_trip() -> None:
    """Launch and process-state messages parse every optional field."""
    spec = InstanceSpec(name="client-1", profile_id="profile", debugger_port=55555)
    assert InstanceSpec.from_mapping(value=spec.to_mapping()) == spec
    snapshot = InstanceSnapshot(
        instance_id="id",
        name="client-1",
        profile_id="profile",
        status=InstanceStatus.RUNNING,
        pid=12,
        started_at="now",
    )
    assert InstanceSnapshot.from_mapping(value=snapshot.to_mapping()) == snapshot
    assert InstanceSnapshot.from_mapping(value={**snapshot.to_mapping(), "pid": None}).pid is None


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "bad/name", "profile_id": "p"},
        {"name": "n", "profile_id": "bad/name"},
        {"name": "n", "profile_id": "p", "width": 639},
        {"name": "n", "profile_id": "p", "height": 479},
        {"name": "n", "profile_id": "p", "debugger_port": 80},
    ],
)
def test_invalid_instance_spec_fields(*, fields: dict[str, object]) -> None:
    """Invalid dataclass construction fails immediately."""
    with pytest.raises(RemoteValidationError):
        InstanceSpec(**fields)  # type: ignore[arg-type]


def test_invalid_instance_wire_values() -> None:
    """Malformed request and response objects fail deterministically."""
    for value in (None, {}, {"name": None, "profile_id": None, "width": object()}):
        with pytest.raises(RemoteValidationError):
            InstanceSpec.from_mapping(value=value)
    for value in (
        None,
        {},
        {
            "instance_id": "x",
            "name": "n",
            "profile_id": "p",
            "status": "wat",
            "pid": object(),
            "started_at": "x",
        },
    ):
        with pytest.raises(RemoteValidationError):
            InstanceSnapshot.from_mapping(value=value)


def test_fingerprint_normalization() -> None:
    """Compact or colon-separated SHA-256 fingerprints normalize identically."""
    compact = "ab" * 32
    normalized = ":".join(["AB"] * 32)
    assert normalize_fingerprint(value=compact) == normalized
    assert normalize_fingerprint(value=normalized) == normalized
    for value in ("", "GG" * 32, "AA" * 31):
        with pytest.raises(RemoteValidationError):
            normalize_fingerprint(value=value)
    with (
        patch(
            "mod_debug_pilot.domain.remote._FINGERPRINT", Mock(fullmatch=Mock(return_value=None))
        ),
        pytest.raises(RemoteValidationError),
    ):
        normalize_fingerprint(value=compact)
