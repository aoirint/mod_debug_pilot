"""Framework-free browser-session workflows."""

from __future__ import annotations

import asyncio
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from mod_debug_pilot.application.pairing import PairingBroker
from mod_debug_pilot.application.ports import AgentRuntime, ProfileImporter, ProfileWorkspace
from mod_debug_pilot.domain import (
    InstanceSnapshot,
    InstanceSpec,
    PairingError,
    PairingRequest,
    ProfileError,
)

_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_LOCAL_MOD: Final = 64 * 1024 * 1024
_MAX_SCREENSHOT: Final = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserContext:
    """Agent-owned services shared by independent browser sessions."""

    pairing: PairingBroker
    importer: ProfileImporter
    workspace: ProfileWorkspace
    runtime: AgentRuntime
    data_root: Path


@dataclass(frozen=True, slots=True, kw_only=True)
class ProfileDraft:
    """User-facing summary of one prepared profile."""

    declared_mods: int
    config_files: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class InstalledProfile:
    """Summary of one verified Agent profile installation."""

    name: str
    file_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadedArtifact:
    """One bounded artifact returned to a browser download prompt."""

    name: str
    content: bytes


class BrowserSession:
    """Own one approved browser session and its disposable profile draft."""

    def __init__(self, *, context: BrowserContext) -> None:
        """Create an unauthorized session with an isolated draft root."""
        self._context = context
        self._pairing: PairingRequest | None = None
        self._approved = False
        self._draft: Path | None = None
        self._source_mods: tuple[str, ...] = ()
        self._session_root = context.data_root / "browser-sessions" / secrets.token_hex(12)

    @property
    def approved(self) -> bool:
        """Return whether the local operator approved this session."""
        return self._approved

    def request_pairing(self, *, code: str, controller_name: str) -> None:
        """Submit the Agent-displayed code for a local decision."""
        self._pairing = self._context.pairing.request(
            code=code,
            controller_name=controller_name,
        )

    def check_pairing(self) -> bool | None:
        """Return the current local decision for this session."""
        if self._pairing is None:
            raise PairingError("Request a connection first.")
        decision = self._context.pairing.status(
            request_id=self._pairing.request_id,
            poll_token=self._pairing.poll_token,
        )
        if decision:
            self._approved = True
        return decision

    async def prepare_profile(
        self,
        *,
        profile_id: str,
        profile_code: str,
        local_mod_name: str,
        local_mod_bytes: bytes,
    ) -> ProfileDraft:
        """Import exact packages and add one browser-selected local DLL."""
        self._require_approved()
        safe_id = _validated_profile_id(value=profile_id)
        safe_name = Path(local_mod_name).name
        if (
            safe_name != local_mod_name
            or Path(safe_name).suffix.casefold() != ".dll"
            or not local_mod_bytes
            or len(local_mod_bytes) > _MAX_LOCAL_MOD
        ):
            raise ProfileError("Select a locally built DLL up to 64 MiB.")
        if self._draft is not None:
            raise ProfileError("Install or close the existing profile draft first.")
        imported = await self._context.importer.import_code(code=profile_code)
        draft = self._session_root / safe_id
        await self._context.workspace.materialize(
            imported=imported,
            destination=draft,
            local_mod_name=safe_name,
            local_mod_bytes=local_mod_bytes,
        )
        self._draft = draft
        self._source_mods = tuple(mod.dependency for mod in imported.mods)
        config_root = draft / "BepInEx" / "config"
        config_files = tuple(
            path.relative_to(config_root).as_posix()
            for path in self._context.workspace.config_files(profile=draft)
        )
        return ProfileDraft(declared_mods=len(imported.mods), config_files=config_files)

    def read_config(self, *, relative: str) -> str:
        """Read one selected configuration from the current draft."""
        return self._context.workspace.read_config(
            profile=self._require_draft(),
            relative=relative,
        )

    def write_config(self, *, relative: str, content: str) -> None:
        """Replace one selected configuration in the current draft."""
        self._context.workspace.write_config(
            profile=self._require_draft(),
            relative=relative,
            content=content,
        )

    async def install_profile(self, *, profile_id: str) -> InstalledProfile:
        """Bundle, verify, and install the current draft on the Agent."""
        self._require_approved()
        safe_id = _validated_profile_id(value=profile_id)
        draft = self._require_draft()
        bundle = self._session_root / f"{safe_id}.mdp-profile"
        manifest = await asyncio.to_thread(
            self._context.workspace.create_bundle,
            profile=draft,
            profile_name=safe_id,
            source_mods=self._source_mods,
            destination=bundle,
        )
        bundle_bytes = await asyncio.to_thread(bundle.read_bytes)
        installed_name = await self._context.runtime.install_profile(
            profile_id=safe_id,
            bundle=bundle_bytes,
        )
        await self._discard_draft()
        return InstalledProfile(name=installed_name, file_count=len(manifest.files))

    async def launch(
        self,
        *,
        name: str,
        profile_id: str,
        debugger_port: int,
    ) -> InstanceSpec:
        """Launch one allow-listed instance and return its effective specification."""
        self._require_approved()
        spec = InstanceSpec(
            name=name,
            profile_id=_validated_profile_id(value=profile_id),
            debugger_port=debugger_port,
        )
        await self._context.runtime.launch(spec=spec)
        return spec

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Return every Agent-tracked instance."""
        self._require_approved()
        return await self._context.runtime.list_instances()

    async def stop(self, *, instance_id: str) -> None:
        """Stop exactly one tracked instance."""
        self._require_approved()
        await self._context.runtime.stop(instance_id=instance_id)

    async def capture(self, *, instance_id: str) -> DownloadedArtifact:
        """Capture and return one bounded screenshot artifact."""
        self._require_approved()
        path = await self._context.runtime.capture(instance_id=instance_id)
        if path.stat().st_size > _MAX_SCREENSHOT:
            raise OSError("Screenshot exceeds the download limit.")
        content = await asyncio.to_thread(path.read_bytes)
        return DownloadedArtifact(name=path.name, content=content)

    async def close(self) -> None:
        """Discard this browser session's unfinished profile data."""
        await self._discard_draft()

    def _require_approved(self) -> None:
        if not self._approved:
            raise PairingError("This browser session is not approved.")

    def _require_draft(self) -> Path:
        if self._draft is None:
            raise ProfileError("Prepare a profile draft first.")
        return self._draft

    async def _discard_draft(self) -> None:
        if self._session_root.exists():
            await asyncio.to_thread(shutil.rmtree, self._session_root)
        self._draft = None
        self._source_mods = ()


def _validated_profile_id(*, value: str) -> str:
    normalized = value.strip()
    if _SAFE_ID.fullmatch(normalized) is None:
        raise ProfileError("Profile ID may contain letters, numbers, dot, dash, and underscore.")
    return normalized
