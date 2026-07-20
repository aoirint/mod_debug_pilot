"""Validated values shared by ModDebugPilot use cases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ValidationError(ValueError):
    """Report one or more invalid user-controlled configuration values."""

    def __init__(self, *, errors: dict[str, str]) -> None:
        """Create an error with stable field-specific messages."""
        super().__init__("Configuration is invalid.")
        self.errors = errors


class JobKind(StrEnum):
    """Allow-listed operations accepted by the application."""

    VALIDATE_ENVIRONMENT = "validate_environment"
    RUN_SMOKE_TEST = "run_smoke_test"


class JobOutcome(StrEnum):
    """Terminal job outcomes written to the artifact result."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True, kw_only=True)
class PilotConfig:
    """Configuration snapshot used by one job."""

    game_executable: str
    base_profile_dir: str
    mod_dll: str
    artifact_root: str
    profile_name: str = "default"
    timeout_seconds: int = 180
    screen_width: int = 1280
    screen_height: int = 720
    ready_marker: str = "Chainloader startup complete"
    screenshot_delay_seconds: float = 2.0
    debugger_port: int = 55555

    @classmethod
    def defaults(cls) -> PilotConfig:
        """Return an intentionally incomplete first-run configuration."""
        return cls(
            game_executable="",
            base_profile_dir="",
            mod_dll="",
            artifact_root="",
        )

    @classmethod
    def from_mapping(cls, *, values: dict[str, object]) -> PilotConfig:
        """Validate serialized or form values and create a configuration."""
        errors: dict[str, str] = {}
        paths = {
            name: _required_text(values=values, name=name, errors=errors, max_length=4096)
            for name in ("game_executable", "base_profile_dir", "mod_dll", "artifact_root")
        }
        profile_name = _required_text(
            values=values,
            name="profile_name",
            errors=errors,
            max_length=64,
        )
        if profile_name and _PROFILE_PATTERN.fullmatch(profile_name) is None:
            errors["profile_name"] = "Use letters, numbers, underscore, or hyphen."

        ready_marker = _required_text(
            values=values,
            name="ready_marker",
            errors=errors,
            max_length=200,
        )
        timeout_seconds = _bounded_int(
            values=values,
            name="timeout_seconds",
            minimum=5,
            maximum=3600,
            errors=errors,
        )
        screen_width = _bounded_int(
            values=values,
            name="screen_width",
            minimum=640,
            maximum=7680,
            errors=errors,
        )
        screen_height = _bounded_int(
            values=values,
            name="screen_height",
            minimum=480,
            maximum=4320,
            errors=errors,
        )
        screenshot_delay_seconds = _bounded_float(
            values=values,
            name="screenshot_delay_seconds",
            minimum=0.0,
            maximum=300.0,
            errors=errors,
        )
        debugger_port = _bounded_int(
            values=values,
            name="debugger_port",
            minimum=1024,
            maximum=65535,
            errors=errors,
        )
        if errors:
            raise ValidationError(errors=errors)
        return cls(
            **paths,
            profile_name=profile_name,
            timeout_seconds=timeout_seconds,
            screen_width=screen_width,
            screen_height=screen_height,
            ready_marker=ready_marker,
            screenshot_delay_seconds=screenshot_delay_seconds,
            debugger_port=debugger_port,
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the stable JSON representation."""
        return {
            "game_executable": self.game_executable,
            "base_profile_dir": self.base_profile_dir,
            "mod_dll": self.mod_dll,
            "artifact_root": self.artifact_root,
            "profile_name": self.profile_name,
            "timeout_seconds": self.timeout_seconds,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "ready_marker": self.ready_marker,
            "screenshot_delay_seconds": self.screenshot_delay_seconds,
            "debugger_port": self.debugger_port,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class JobRequest:
    """Immutable request handed to the trusted runner."""

    job_id: str
    kind: JobKind
    created_at: str
    config: PilotConfig

    def to_mapping(self) -> dict[str, object]:
        """Return the stable artifact representation."""
        return {
            "schema_version": 1,
            "job_id": self.job_id,
            "job": self.kind.value,
            "created_at": self.created_at,
            "config": self.config.to_mapping(),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class JobResult:
    """Terminal result returned by a runner."""

    job_id: str
    outcome: JobOutcome
    message: str
    artifact_dir: str
    started_at: str
    finished_at: str

    @property
    def succeeded(self) -> bool:
        """Return whether the job completed successfully."""
        return self.outcome is JobOutcome.SUCCEEDED

    def to_mapping(self) -> dict[str, object]:
        """Return the stable artifact representation."""
        return {
            "schema_version": 1,
            "job_id": self.job_id,
            "outcome": self.outcome.value,
            "message": self.message,
            "artifact_dir": self.artifact_dir,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _required_text(
    *,
    values: dict[str, object],
    name: str,
    errors: dict[str, str],
    max_length: int,
) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        errors[name] = "This field is required."
        return ""
    normalized = value.strip()
    if len(normalized) > max_length:
        errors[name] = f"Use at most {max_length} characters."
        return ""
    return normalized


def _bounded_int(
    *,
    values: dict[str, object],
    name: str,
    minimum: int,
    maximum: int,
    errors: dict[str, str],
) -> int:
    raw = values.get(name)
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        errors[name] = "Enter a whole number."
        return minimum
    if not minimum <= value <= maximum:
        errors[name] = f"Enter a value from {minimum} to {maximum}."
    return value


def _bounded_float(
    *,
    values: dict[str, object],
    name: str,
    minimum: float,
    maximum: float,
    errors: dict[str, str],
) -> float:
    raw = values.get(name)
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        errors[name] = "Enter a number."
        return minimum
    if not minimum <= value <= maximum:
        errors[name] = f"Enter a value from {minimum:g} to {maximum:g}."
    return value
