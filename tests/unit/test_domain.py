"""Tests for framework-independent ModDebugPilot values."""

from __future__ import annotations

import pytest

from mod_debug_pilot.domain import (
    JobKind,
    JobOutcome,
    JobRequest,
    JobResult,
    PilotConfig,
    ValidationError,
)


def valid_values() -> dict[str, object]:
    """Return one complete serialized configuration."""
    return {
        "game_executable": r"C:\Games\Lethal Company.exe",
        "base_profile_dir": r"C:\Profiles\base",
        "mod_dll": r"C:\Build\Example.dll",
        "artifact_root": r"C:\Artifacts",
        "profile_name": "smoke_1",
        "timeout_seconds": "45",
        "screen_width": "1920",
        "screen_height": 1080,
        "ready_marker": "[AUTOTEST] ready",
        "screenshot_delay_seconds": "1.5",
        "debugger_port": "55556",
    }


def test_config_round_trip_and_defaults() -> None:
    """Configuration normalizes form strings and has incomplete first-run defaults."""
    config = PilotConfig.from_mapping(valid_values())

    assert config.to_mapping() == {
        "game_executable": r"C:\Games\Lethal Company.exe",
        "base_profile_dir": r"C:\Profiles\base",
        "mod_dll": r"C:\Build\Example.dll",
        "artifact_root": r"C:\Artifacts",
        "profile_name": "smoke_1",
        "timeout_seconds": 45,
        "screen_width": 1920,
        "screen_height": 1080,
        "ready_marker": "[AUTOTEST] ready",
        "screenshot_delay_seconds": 1.5,
        "debugger_port": 55556,
    }
    assert PilotConfig.from_mapping(config.to_mapping()) == config
    assert PilotConfig.defaults().game_executable == ""


@pytest.mark.parametrize(
    ("change", "field", "message"),
    [
        ({"game_executable": None}, "game_executable", "This field is required."),
        ({"base_profile_dir": "  "}, "base_profile_dir", "This field is required."),
        ({"mod_dll": "x" * 4097}, "mod_dll", "Use at most 4096 characters."),
        ({"profile_name": "bad profile"}, "profile_name", "Use letters"),
        ({"profile_name": "x" * 65}, "profile_name", "Use at most 64 characters."),
        ({"ready_marker": "x" * 201}, "ready_marker", "Use at most 200 characters."),
        ({"timeout_seconds": "later"}, "timeout_seconds", "Enter a whole number."),
        ({"timeout_seconds": 4}, "timeout_seconds", "from 5 to 3600"),
        ({"screen_width": 7681}, "screen_width", "from 640 to 7680"),
        ({"screen_height": 479}, "screen_height", "from 480 to 4320"),
        (
            {"screenshot_delay_seconds": "later"},
            "screenshot_delay_seconds",
            "Enter a number.",
        ),
        (
            {"screenshot_delay_seconds": 301},
            "screenshot_delay_seconds",
            "from 0 to 300",
        ),
        ({"debugger_port": 1023}, "debugger_port", "from 1024 to 65535"),
    ],
)
def test_config_rejects_invalid_fields(
    change: dict[str, object],
    field: str,
    message: str,
) -> None:
    """Every bounded or required input returns a stable field error."""
    values = valid_values()
    values.update(change)

    with pytest.raises(ValidationError) as raised:
        PilotConfig.from_mapping(values)

    assert str(raised.value) == "Configuration is invalid."
    assert message in raised.value.errors[field]


def test_request_and_result_artifact_mappings() -> None:
    """Requests and results serialize stable schema-versioned records."""
    config = PilotConfig.from_mapping(valid_values())
    request = JobRequest(
        job_id="job-1",
        kind=JobKind.RUN_SMOKE_TEST,
        created_at="2026-07-20T00:00:00+00:00",
        config=config,
    )
    success = JobResult(
        job_id="job-1",
        outcome=JobOutcome.SUCCEEDED,
        message="Done.",
        artifact_dir=r"C:\Artifacts\job-1",
        started_at="start",
        finished_at="finish",
    )
    failure = JobResult(
        job_id="job-2",
        outcome=JobOutcome.FAILED,
        message="Failed.",
        artifact_dir="",
        started_at="start",
        finished_at="finish",
    )

    assert request.to_mapping()["job"] == "run_smoke_test"
    assert request.to_mapping()["config"] == config.to_mapping()
    assert success.succeeded is True
    assert failure.succeeded is False
    assert success.to_mapping() == {
        "schema_version": 1,
        "job_id": "job-1",
        "outcome": "succeeded",
        "message": "Done.",
        "artifact_dir": r"C:\Artifacts\job-1",
        "started_at": "start",
        "finished_at": "finish",
    }
