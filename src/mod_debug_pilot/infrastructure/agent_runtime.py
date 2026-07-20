"""Controlled-side profile storage, save isolation, and instance lifecycle."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from mod_debug_pilot.domain import InstanceSnapshot, InstanceSpec, InstanceStatus
from mod_debug_pilot.infrastructure.profiles import ProfileImportError, extract_bundle
from mod_debug_pilot.infrastructure.runner import (
    AsyncioProcessLauncher,
    PillowScreenCapturer,
    ProcessLauncher,
    RunningProcess,
    ScreenCapturer,
)
from mod_debug_pilot.infrastructure.settings import write_json_atomic

_ENVIRONMENT_KEYS = frozenset(
    {
        "APPDATA",
        "LOCALAPPDATA",
        "PATH",
        "PROGRAMDATA",
        "STEAMAPPID",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)
_SAVE_REDIRECT_READY = "[MODDEBUGPILOT] save_redirect_ready"
_SAVE_REDIRECT_TIMEOUT_SECONDS = 30.0


class AgentRuntimeError(OSError):
    """Report a safe controlled-side lifecycle failure."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRuntimeConfig:
    """Local paths and limits owned by the controlled workstation."""

    game_executable: Path
    data_root: Path
    artifact_root: Path
    save_directory: Path
    max_upload_bytes: int = 512 * 1024 * 1024

    def validate(self, *, platform_name: str) -> None:
        """Validate that mutable roots cannot overlap protected inputs."""
        if platform_name != "win32":
            raise AgentRuntimeError("The remote agent requires Windows.")
        game = self.game_executable.resolve()
        game_dir = game.parent
        roots = (self.data_root.resolve(), self.artifact_root.resolve())
        if not game.is_file():
            raise AgentRuntimeError("Lethal Company executable was not found.")
        if self.max_upload_bytes < 1024 * 1024 or self.max_upload_bytes > 2 * 1024**3:
            raise AgentRuntimeError("Upload size limit is invalid.")
        for root in roots:
            if root == game_dir or game_dir in root.parents or root in game_dir.parents:
                raise AgentRuntimeError("Agent data roots must be outside the game directory.")
        if self.save_directory.resolve() in roots:
            raise AgentRuntimeError("Save directory cannot be an agent data root.")


class ProcessProbe(Protocol):
    """Detect an already-running normal game before isolation begins."""

    async def image_is_running(self, image_name: str) -> bool:
        """Return whether Windows reports the executable image."""
        ...


class TasklistProcessProbe:
    """Query Windows `tasklist` without a shell or wildcard target."""

    async def image_is_running(self, image_name: str) -> bool:
        """Match the exact first CSV field returned by tasklist."""
        process = await asyncio.create_subprocess_exec(
            "tasklist",
            "/FI",
            f"IMAGENAME eq {image_name}",
            "/FO",
            "CSV",
            "/NH",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            raise AgentRuntimeError("Could not inspect running game processes.")
        rows = list(csv.reader(io.StringIO(stdout.decode(errors="replace"))))
        return any(row and row[0].casefold() == image_name.casefold() for row in rows)


@dataclass(slots=True, kw_only=True)
class _Instance:
    spec: InstanceSpec
    snapshot: InstanceSnapshot
    process: RunningProcess
    profile_dir: Path
    artifact_dir: Path


class SaveIsolation:
    """Transactionally hide normal saves while debug instances are active."""

    def __init__(self, *, save_directory: Path, state_root: Path) -> None:
        """Create a journaled isolation scope."""
        self._save = save_directory
        self._state_root = state_root
        self._journal = state_root / "save-isolation.json"
        self._backup = save_directory.with_name(save_directory.name + ".moddebugpilot-normal")
        self._active = False

    @property
    def active(self) -> bool:
        """Return whether this process owns an active isolation scope."""
        return self._active

    def recover(self) -> None:
        """Restore a journaled normal-save directory after an interrupted run."""
        if not self._journal.is_file():
            return
        try:
            payload = json.loads(self._journal.read_text(encoding="utf-8"))
            original_existed = payload["original_existed"]
            session_id = payload["session_id"]
            if not isinstance(original_existed, bool) or not isinstance(session_id, str):
                raise TypeError
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise AgentRuntimeError(
                "Save-isolation journal is invalid; recovery stopped."
            ) from error
        self._restore_files(original_existed=original_existed, session_id=session_id)

    def activate(self) -> None:
        """Move normal saves aside and create an empty debug save directory."""
        if self._active:
            return
        self.recover()
        if self._backup.exists():
            raise AgentRuntimeError("A save backup already exists; refusing to overwrite it.")
        self._state_root.mkdir(parents=True, exist_ok=True)
        session_id = uuid4().hex
        original_existed = self._save.exists()
        write_json_atomic(
            self._journal,
            payload={
                "schema_version": 1,
                "session_id": session_id,
                "original_existed": original_existed,
            },
        )
        try:
            if original_existed:
                self._save.parent.mkdir(parents=True, exist_ok=True)
                self._save.replace(self._backup)
            self._save.mkdir(parents=True, exist_ok=False)
        except BaseException:
            self._restore_files(original_existed=original_existed, session_id=session_id)
            raise
        self._active = True

    def restore(self) -> None:
        """Archive debug saves and put normal saves back without operator steps."""
        if not self._active and not self._journal.exists():
            return
        payload = json.loads(self._journal.read_text(encoding="utf-8"))
        self._restore_files(
            original_existed=bool(payload["original_existed"]),
            session_id=str(payload["session_id"]),
        )
        self._active = False

    def _restore_files(self, *, original_existed: bool, session_id: str) -> None:
        debug_archive = self._state_root / "debug-saves" / session_id
        if self._save.exists():
            debug_archive.parent.mkdir(parents=True, exist_ok=True)
            if debug_archive.exists():
                raise AgentRuntimeError("Debug-save archive collision prevented recovery.")
            self._save.replace(debug_archive)
        if original_existed:
            if not self._backup.exists():
                raise AgentRuntimeError("Normal-save backup is missing; recovery stopped.")
            self._backup.replace(self._save)
        elif self._backup.exists():
            raise AgentRuntimeError("Unexpected save backup prevented recovery.")
        self._journal.unlink(missing_ok=True)


class BootstrapIsolation:
    """Journal and restore the two Doorstop files shared by all instances."""

    _NAMES = ("winhttp.dll", "doorstop_config.ini")

    def __init__(self, *, game_directory: Path, state_root: Path) -> None:
        """Create a game-directory bootstrap transaction."""
        self._game = game_directory
        self._state_root = state_root
        self._journal = state_root / "bootstrap-isolation.json"
        self._backup = state_root / "bootstrap-backup"
        self._active = False
        self._source_hashes: tuple[str, ...] | None = None

    def recover(self) -> None:
        """Restore originals after an interrupted agent run."""
        if not self._journal.is_file():
            return
        self._restore_files()

    def activate(self, profile: Path) -> None:
        """Install one bootstrap or verify compatibility with the active one."""
        hashes = tuple(_sha256(profile / name) for name in self._NAMES)
        if self._active:
            if hashes != self._source_hashes:
                raise AgentRuntimeError("Active instances require identical Doorstop bootstraps.")
            return
        self.recover()
        if self._backup.exists():
            raise AgentRuntimeError("A bootstrap backup already exists.")
        self._backup.mkdir(parents=True)
        write_json_atomic(
            self._journal,
            payload={"schema_version": 1, "files": list(self._NAMES)},
        )
        try:
            for name in self._NAMES:
                target = self._game / name
                if target.exists():
                    target.replace(self._backup / name)
                shutil.copy2(profile / name, target)
        except BaseException:
            self._restore_files()
            raise
        self._source_hashes = hashes
        self._active = True

    def restore(self) -> None:
        """Restore originals when the final instance stops."""
        if not self._active and not self._journal.exists():
            return
        self._restore_files()
        self._active = False
        self._source_hashes = None

    def _restore_files(self) -> None:
        for name in self._NAMES:
            target = self._game / name
            target.unlink(missing_ok=True)
            backup = self._backup / name
            if backup.exists():
                backup.replace(target)
        if self._backup.exists():
            self._backup.rmdir()
        self._journal.unlink(missing_ok=True)


class RemoteAgentRuntime:
    """Own installed profiles and exact launched process handles."""

    def __init__(
        self,
        config: AgentRuntimeConfig,
        *,
        launcher: ProcessLauncher,
        capturer: ScreenCapturer,
        process_probe: ProcessProbe,
        environment: Mapping[str, str],
        platform_name: str,
    ) -> None:
        """Create a runtime with explicit system-effect adapters."""
        config.validate(platform_name=platform_name)
        self._config = config
        self._launcher = launcher
        self._capturer = capturer
        self._probe = process_probe
        self._environment = dict(environment)
        self._instances: dict[str, _Instance] = {}
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._save = SaveIsolation(
            save_directory=config.save_directory,
            state_root=config.data_root,
        )
        self._bootstrap = BootstrapIsolation(
            game_directory=config.game_executable.parent,
            state_root=config.data_root,
        )

    @classmethod
    def system_default(cls, config: AgentRuntimeConfig) -> RemoteAgentRuntime:
        """Create the production controlled-side adapter set."""
        return cls(
            config,
            launcher=AsyncioProcessLauncher(),
            capturer=PillowScreenCapturer(),
            process_probe=TasklistProcessProbe(),
            environment=os.environ,
            platform_name=sys.platform,
        )

    async def recover(self) -> None:
        """Restore any interrupted bootstrap and normal-save transactions."""
        async with self._lock:
            await asyncio.to_thread(self._bootstrap.recover)
            await asyncio.to_thread(self._save.recover)

    async def install_profile(self, profile_id: str, *, bundle: bytes) -> str:
        """Validate and install one immutable controller-built profile."""
        if len(bundle) > self._config.max_upload_bytes:
            raise ProfileImportError("Uploaded profile is too large.")
        if not _safe_identifier(profile_id):
            raise ProfileImportError("Profile identifier is invalid.")
        destination = self._config.data_root / "profiles" / profile_id
        manifest = await asyncio.to_thread(extract_bundle, bundle, destination=destination)
        return manifest.profile_name

    async def launch(self, spec: InstanceSpec) -> InstanceSnapshot:
        """Launch one profile with isolated logs and shared protected debug saves."""
        async with self._lock:
            source = self._config.data_root / "profiles" / spec.profile_id
            if not source.is_dir():
                raise AgentRuntimeError("Requested profile is not installed.")
            if any(
                item.snapshot.status in {InstanceStatus.STARTING, InstanceStatus.RUNNING}
                and item.spec.debugger_port == spec.debugger_port
                for item in self._instances.values()
            ):
                raise AgentRuntimeError("Debugger port is already in use by another instance.")
            if not self._has_live_instances():
                if await self._probe.image_is_running(self._config.game_executable.name):
                    raise AgentRuntimeError("Lethal Company is already running outside the agent.")
                await asyncio.to_thread(self._save.activate)
            instance_id = uuid4().hex
            artifact_dir = self._config.artifact_root / instance_id
            profile_dir = artifact_dir / "profile"
            artifact_dir.mkdir(parents=True, exist_ok=False)
            process: RunningProcess | None = None
            try:
                await asyncio.to_thread(shutil.copytree, source, profile_dir)
                await asyncio.to_thread(self._bootstrap.activate, profile_dir)
                preloader = profile_dir / "BepInEx" / "core" / "BepInEx.Preloader.dll"
                arguments = (
                    "-screen-fullscreen",
                    "0",
                    "-screen-width",
                    str(spec.width),
                    "-screen-height",
                    str(spec.height),
                    "--doorstop-enable",
                    "true",
                    "--doorstop-target",
                    str(preloader),
                )
                environment = {
                    key: value
                    for key, value in self._environment.items()
                    if key.upper() in _ENVIRONMENT_KEYS
                }
                environment["MONO_ENV_OPTIONS"] = (
                    "--debugger-agent=transport=dt_socket,server=y,"
                    f"address=127.0.0.1:{spec.debugger_port},embedding=1,defer=y"
                )
                environment["MODDEBUGPILOT_SAVE_ROOT"] = str(
                    self._config.data_root / "instance-saves" / instance_id
                )
                process = await self._launcher.launch(
                    self._config.game_executable,
                    arguments=arguments,
                    working_directory=self._config.game_executable.parent,
                    environment=environment,
                )
                if process is None:
                    raise AgentRuntimeError("Game process was not created.")
                await self._wait_for_save_redirect(process, profile_dir=profile_dir)
            except BaseException:
                if process is not None:
                    await process.terminate_tree()
                shutil.rmtree(artifact_dir, ignore_errors=True)
                if not self._has_live_instances():
                    await asyncio.to_thread(self._bootstrap.restore)
                    await asyncio.to_thread(self._save.restore)
                raise
            snapshot = InstanceSnapshot(
                instance_id=instance_id,
                name=spec.name,
                profile_id=spec.profile_id,
                status=InstanceStatus.RUNNING,
                pid=process.pid,
                started_at=datetime.now(UTC).isoformat(),
            )
            instance = _Instance(
                spec=spec,
                snapshot=snapshot,
                process=process,
                profile_dir=profile_dir,
                artifact_dir=artifact_dir,
            )
            self._instances[instance_id] = instance
            self._watchers[instance_id] = asyncio.create_task(
                self._watch_instance(instance_id),
                name=f"moddebugpilot-instance-{instance_id}",
            )
            write_json_atomic(artifact_dir / "instance.json", payload=snapshot.to_mapping())
            return snapshot

    async def _wait_for_save_redirect(
        self,
        process: RunningProcess,
        *,
        profile_dir: Path,
    ) -> None:
        """Fail closed unless the per-instance ES3 redirector reports ready."""
        log_path = profile_dir / "BepInEx" / "LogOutput.log"
        deadline = asyncio.get_running_loop().time() + _SAVE_REDIRECT_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise AgentRuntimeError("Game exited before save isolation was ready.")
            if await asyncio.to_thread(
                _file_contains,
                log_path,
                marker=_SAVE_REDIRECT_READY,
            ):
                return
            await asyncio.sleep(0.2)
        await process.terminate_tree()
        raise AgentRuntimeError("Save isolation did not become ready; instance was stopped.")

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Return all process records, including terminal records."""
        async with self._lock:
            self._refresh_exited()
            await self._finalize_if_idle()
            return tuple(item.snapshot for item in self._instances.values())

    async def stop(self, instance_id: str) -> InstanceSnapshot:
        """Terminate exactly one tracked process tree and finalize isolation if last."""
        async with self._lock:
            instance = self._instances.get(instance_id)
            if instance is None:
                raise AgentRuntimeError("Instance was not found.")
            if instance.snapshot.status not in {InstanceStatus.STOPPED, InstanceStatus.FAILED}:
                await instance.process.terminate_tree()
                self._set_status(
                    instance,
                    status=InstanceStatus.STOPPED,
                    message="Stopped by operator.",
                )
            await self._finalize_if_idle()
            return instance.snapshot

    async def capture(self, instance_id: str) -> Path:
        """Capture the controlled desktop into the selected instance artifacts."""
        async with self._lock:
            instance = self._instances.get(instance_id)
            if instance is None:
                raise AgentRuntimeError("Instance was not found.")
            destination = (
                instance.artifact_dir
                / "screenshots"
                / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + ".png")
            )
            await self._capturer.capture(destination)
            return destination

    async def shutdown(self) -> None:
        """Stop every live process and restore normal workstation state."""
        async with self._lock:
            for instance in self._instances.values():
                if instance.snapshot.status in {InstanceStatus.STARTING, InstanceStatus.RUNNING}:
                    try:
                        await instance.process.terminate_tree()
                        self._set_status(
                            instance,
                            status=InstanceStatus.STOPPED,
                            message="Stopped with agent.",
                        )
                    except OSError:
                        self._set_status(
                            instance,
                            status=InstanceStatus.FAILED,
                            message="Process cleanup failed.",
                        )
            await asyncio.to_thread(self._bootstrap.restore)
            await asyncio.to_thread(self._save.restore)
        for watcher in self._watchers.values():
            watcher.cancel()
        await asyncio.gather(*self._watchers.values(), return_exceptions=True)
        self._watchers.clear()

    def artifact(self, instance_id: str, *, relative: str) -> Path:
        """Resolve one regular artifact without allowing traversal."""
        instance = self._instances.get(instance_id)
        if instance is None:
            raise AgentRuntimeError("Instance was not found.")
        target = _confined(instance.artifact_dir, relative=relative)
        if not target.is_file():
            raise AgentRuntimeError("Artifact was not found.")
        return target

    def _refresh_exited(self) -> None:
        for instance in self._instances.values():
            if (
                instance.snapshot.status is InstanceStatus.RUNNING
                and instance.process.returncode is not None
            ):
                self._set_status(
                    instance,
                    status=(
                        InstanceStatus.STOPPED
                        if instance.process.returncode == 0
                        else InstanceStatus.FAILED
                    ),
                    message=f"Process exited with code {instance.process.returncode}.",
                )

    async def _finalize_if_idle(self) -> None:
        self._refresh_exited()
        if self._has_live_instances():
            return
        await asyncio.to_thread(self._bootstrap.restore)
        await asyncio.to_thread(self._save.restore)

    async def _watch_instance(self, instance_id: str) -> None:
        """Notice natural process exit and restore the last active session."""
        try:
            while True:
                await asyncio.sleep(0.5)
                async with self._lock:
                    instance = self._instances.get(instance_id)
                    if instance is None or instance.snapshot.status is not InstanceStatus.RUNNING:
                        return
                    if instance.process.returncode is None:
                        continue
                    self._refresh_exited()
                    await self._finalize_if_idle()
                    return
        except asyncio.CancelledError:
            return

    def _has_live_instances(self) -> bool:
        return any(
            item.snapshot.status in {InstanceStatus.STARTING, InstanceStatus.RUNNING}
            for item in self._instances.values()
        )

    @staticmethod
    def _set_status(
        instance: _Instance,
        *,
        status: InstanceStatus,
        message: str,
    ) -> None:
        instance.snapshot = InstanceSnapshot(
            instance_id=instance.snapshot.instance_id,
            name=instance.snapshot.name,
            profile_id=instance.snapshot.profile_id,
            status=status,
            pid=instance.snapshot.pid,
            started_at=instance.snapshot.started_at,
            message=message,
        )
        log = instance.profile_dir / "BepInEx" / "LogOutput.log"
        if log.is_file():
            shutil.copy2(log, instance.artifact_dir / "game.log")
        write_json_atomic(
            instance.artifact_dir / "instance.json",
            payload=instance.snapshot.to_mapping(),
        )


def _safe_identifier(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 64
        and all(
            character.isascii() and (character.isalnum() or character in "_.-")
            for character in value
        )
    )


def _confined(root: Path, *, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved = root.resolve()
    if candidate == resolved or resolved not in candidate.parents:
        raise AgentRuntimeError("Artifact path escapes its instance root.")
    return candidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_contains(path: Path, *, marker: str) -> bool:
    if not path.is_file():
        return False
    try:
        return marker in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
