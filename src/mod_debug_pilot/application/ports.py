"""Typed effect boundaries used by application and presentation code."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from mod_debug_pilot.domain import (
    AgentSettings,
    BundleManifest,
    ImportedProfile,
    InstanceSnapshot,
    InstanceSpec,
    PairingRequest,
)


class ProfileImporter(Protocol):
    """Resolve one Thunderstore Profile Code."""

    async def import_code(self, *, code: str) -> ImportedProfile:
        """Return one validated export."""
        ...


class ProfileWorkspace(Protocol):
    """Materialize and edit a disposable profile."""

    async def materialize(
        self,
        *,
        imported: ImportedProfile,
        destination: Path,
        local_mod_name: str,
        local_mod_bytes: bytes,
    ) -> None:
        """Create one profile from exact package and local-build inputs."""
        ...

    def config_files(self, *, profile: Path) -> tuple[Path, ...]:
        """List editable configuration files."""
        ...

    def read_config(self, *, profile: Path, relative: str) -> str:
        """Read one editable configuration."""
        ...

    def write_config(self, *, profile: Path, relative: str, content: str) -> None:
        """Replace one editable configuration."""
        ...

    def create_bundle(
        self,
        *,
        profile: Path,
        profile_name: str,
        source_mods: Iterable[str],
        destination: Path,
    ) -> BundleManifest:
        """Create one verified profile bundle."""
        ...


class AgentRuntime(Protocol):
    """Install profiles and own tracked game instances."""

    async def install_profile(self, *, profile_id: str, bundle: bytes) -> str:
        """Install one immutable profile."""
        ...

    async def launch(self, *, spec: InstanceSpec) -> InstanceSnapshot:
        """Launch one tracked game instance."""
        ...

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Return tracked instance state."""
        ...

    async def stop(self, *, instance_id: str) -> InstanceSnapshot:
        """Stop exactly one tracked instance."""
        ...

    async def capture(self, *, instance_id: str) -> Path:
        """Capture one screenshot artifact."""
        ...

    async def shutdown(self) -> None:
        """Stop instances and restore shared workstation state."""
        ...


class WebHost(Protocol):
    """Own the Agent-hosted browser listener."""

    async def start(self, *, host: str, port: int) -> None:
        """Start accepting browser sessions."""
        ...

    async def stop(self) -> None:
        """Stop accepting browser sessions."""
        ...


class AgentHost(Protocol):
    """Own the controlled workstation service lifecycle."""

    def load_settings(self) -> AgentSettings:
        """Return persisted settings or safe defaults."""
        ...

    async def start(self, *, settings: AgentSettings) -> str:
        """Start the HTTP controller and return its URL."""
        ...

    async def stop(self) -> None:
        """Stop the controller and restore workstation state."""
        ...

    def open_pairing(self) -> str:
        """Open and return a one-time pairing code."""
        ...

    def pending_pairings(self) -> tuple[PairingRequest, ...]:
        """Return requests awaiting an operator decision."""
        ...

    def decide_pairing(self, *, request_id: str, approve: bool) -> None:
        """Apply one explicit operator decision."""
        ...

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Return tracked instances."""
        ...

    async def stop_instance(self, *, instance_id: str) -> None:
        """Stop exactly one tracked instance."""
        ...
