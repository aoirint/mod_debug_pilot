"""Validated contracts shared by the controller and remote agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT = re.compile(r"^(?:[0-9A-F]{2}:){31}[0-9A-F]{2}$")


class RemoteValidationError(ValueError):
    """Reject an invalid remote protocol or bundle value."""


class InstanceStatus(StrEnum):
    """Lifecycle states visible in both applications."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentSettings:
    """Persisted non-secret settings for the controlled workstation."""

    agent_name: str = "Lethal Company test agent"
    bind_host: str = "0.0.0.0"  # noqa: S104 - Operator-started LAN listener.
    api_port: int = 48950
    web_port: int = 48951
    game_executable: str = ""
    data_root: str = ""
    artifact_root: str = ""
    save_directory: str = ""

    @classmethod
    def from_mapping(cls, *, value: object) -> AgentSettings:
        """Parse and validate a settings form or JSON object."""
        if not isinstance(value, dict):
            raise RemoteValidationError("Agent settings must be an object.")
        fields = (
            "agent_name",
            "bind_host",
            "game_executable",
            "data_root",
            "artifact_root",
            "save_directory",
        )
        text_values: dict[str, str] = {}
        for field in fields:
            raw = value.get(field, getattr(cls(), field))
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
                raise RemoteValidationError(f"Agent setting {field} is invalid.")
            text_values[field] = raw.strip()
        try:
            api_port = int(value.get("api_port", 48950))
            web_port = int(value.get("web_port", 48951))
        except (TypeError, ValueError) as error:
            raise RemoteValidationError("Agent ports must be whole numbers.") from error
        if not 1024 <= api_port <= 65535 or not 1024 <= web_port <= 65535 or api_port == web_port:
            raise RemoteValidationError("Agent ports must be distinct values from 1024 to 65535.")
        return cls(**text_values, api_port=api_port, web_port=web_port)

    def to_mapping(self) -> dict[str, object]:
        """Return the non-secret stable JSON representation."""
        return {
            "schema_version": 1,
            "agent_name": self.agent_name,
            "bind_host": self.bind_host,
            "api_port": self.api_port,
            "web_port": self.web_port,
            "game_executable": self.game_executable,
            "data_root": self.data_root,
            "artifact_root": self.artifact_root,
            "save_directory": self.save_directory,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class FileRecord:
    """One immutable file declared by a profile bundle."""

    path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        """Validate path confinement and digest shape."""
        path = PurePosixPath(self.path)
        if (
            not self.path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.path
            or self.size < 0
            or _SHA256.fullmatch(self.sha256) is None
        ):
            raise RemoteValidationError("Invalid bundle file record.")

    def to_mapping(self) -> dict[str, object]:
        """Return the stable wire representation."""
        return {"path": self.path, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_mapping(cls, *, value: object) -> FileRecord:
        """Parse an untrusted wire representation."""
        if not isinstance(value, dict):
            raise RemoteValidationError("Bundle file record must be an object.")
        path = value.get("path")
        size = value.get("size")
        sha256 = value.get("sha256")
        if not isinstance(path, str) or not isinstance(size, int) or not isinstance(sha256, str):
            raise RemoteValidationError("Bundle file record has invalid fields.")
        return cls(path=path, size=size, sha256=sha256)


@dataclass(frozen=True, slots=True, kw_only=True)
class BundleManifest:
    """Controller-produced allow-listed profile bundle manifest."""

    profile_name: str
    created_at: str
    files: tuple[FileRecord, ...]
    source_mods: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        """Enforce stable names and unique file paths."""
        paths = [item.path.casefold() for item in self.files]
        if (
            self.schema_version != 1
            or _SAFE_NAME.fullmatch(self.profile_name) is None
            or not self.created_at
            or len(paths) != len(set(paths))
        ):
            raise RemoteValidationError("Invalid bundle manifest.")

    def to_mapping(self) -> dict[str, object]:
        """Return canonical-JSON-compatible data."""
        return {
            "schema_version": self.schema_version,
            "profile_name": self.profile_name,
            "created_at": self.created_at,
            "source_mods": list(self.source_mods),
            "files": [item.to_mapping() for item in self.files],
        }

    @classmethod
    def from_mapping(cls, *, value: object) -> BundleManifest:
        """Parse an untrusted manifest object."""
        if not isinstance(value, dict):
            raise RemoteValidationError("Bundle manifest must be an object.")
        raw_files = value.get("files")
        raw_mods = value.get("source_mods", [])
        if not isinstance(raw_files, list) or not isinstance(raw_mods, list):
            raise RemoteValidationError("Bundle manifest lists are invalid.")
        profile_name = value.get("profile_name")
        created_at = value.get("created_at")
        schema_version = value.get("schema_version")
        if (
            not isinstance(profile_name, str)
            or not isinstance(created_at, str)
            or not isinstance(schema_version, int)
            or not all(isinstance(item, str) for item in raw_mods)
        ):
            raise RemoteValidationError("Bundle manifest fields are invalid.")
        return cls(
            profile_name=profile_name,
            created_at=created_at,
            schema_version=schema_version,
            files=tuple(FileRecord.from_mapping(value=item) for item in raw_files),
            source_mods=tuple(raw_mods),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class InstanceSpec:
    """Allow-listed launch parameters for one game instance."""

    name: str
    profile_id: str
    width: int = 1280
    height: int = 720
    debugger_port: int = 55555

    def __post_init__(self) -> None:
        """Reject shell-like or out-of-range input."""
        if (
            _SAFE_NAME.fullmatch(self.name) is None
            or _SAFE_NAME.fullmatch(self.profile_id) is None
            or not 640 <= self.width <= 7680
            or not 480 <= self.height <= 4320
            or not 1024 <= self.debugger_port <= 65535
        ):
            raise RemoteValidationError("Invalid instance specification.")

    def to_mapping(self) -> dict[str, object]:
        """Return the wire representation."""
        return {
            "name": self.name,
            "profile_id": self.profile_id,
            "width": self.width,
            "height": self.height,
            "debugger_port": self.debugger_port,
        }

    @classmethod
    def from_mapping(cls, *, value: object) -> InstanceSpec:
        """Parse an untrusted launch request."""
        if not isinstance(value, dict):
            raise RemoteValidationError("Instance specification must be an object.")
        try:
            return cls(
                name=str(value["name"]),
                profile_id=str(value["profile_id"]),
                width=int(value.get("width", 1280)),
                height=int(value.get("height", 720)),
                debugger_port=int(value.get("debugger_port", 55555)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RemoteValidationError("Invalid instance specification fields.") from error


@dataclass(frozen=True, slots=True, kw_only=True)
class InstanceSnapshot:
    """Remote instance state returned to either GUI."""

    instance_id: str
    name: str
    profile_id: str
    status: InstanceStatus
    pid: int | None
    started_at: str
    message: str = ""

    def to_mapping(self) -> dict[str, object]:
        """Return the wire representation."""
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "profile_id": self.profile_id,
            "status": self.status.value,
            "pid": self.pid,
            "started_at": self.started_at,
            "message": self.message,
        }

    @classmethod
    def from_mapping(cls, *, value: object) -> InstanceSnapshot:
        """Parse one untrusted instance response."""
        if not isinstance(value, dict):
            raise RemoteValidationError("Instance snapshot must be an object.")
        try:
            pid_value = value.get("pid")
            pid = None if pid_value is None else int(pid_value)
            return cls(
                instance_id=str(value["instance_id"]),
                name=str(value["name"]),
                profile_id=str(value["profile_id"]),
                status=InstanceStatus(str(value["status"])),
                pid=pid,
                started_at=str(value["started_at"]),
                message=str(value.get("message", "")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RemoteValidationError("Instance snapshot fields are invalid.") from error


def normalize_fingerprint(*, value: str) -> str:
    """Normalize and validate a SHA-256 certificate fingerprint."""
    compact = value.replace(":", "").replace(" ", "").upper()
    if len(compact) != 64 or re.fullmatch(r"[0-9A-F]{64}", compact) is None:
        raise RemoteValidationError("Enter a SHA-256 certificate fingerprint.")
    normalized = ":".join(compact[index : index + 2] for index in range(0, 64, 2))
    if _FINGERPRINT.fullmatch(normalized) is None:
        raise RemoteValidationError("Enter a SHA-256 certificate fingerprint.")
    return normalized
