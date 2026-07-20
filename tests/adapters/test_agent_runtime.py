"""Controlled Agent validation, save recovery, and exact process lifecycle tests."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from mod_debug_pilot.domain import InstanceSpec, InstanceStatus
from mod_debug_pilot.infrastructure.agent_runtime import (
    AgentRuntimeConfig,
    AgentRuntimeError,
    BootstrapIsolation,
    RemoteAgentRuntime,
    SaveIsolation,
    TasklistProcessProbe,
    _confined,
    _file_contains,
    _safe_identifier,
)
from mod_debug_pilot.infrastructure.profiles import ProfileImportError, ProfileWorkspace
from mod_debug_pilot.infrastructure.runner import RunningProcess


class FakeProcess:
    """Controllable tracked process."""

    def __init__(self, *, returncode: int | None = None, terminate_error: bool = False) -> None:
        """Configure process exit and cleanup behavior."""
        self.code = returncode
        self.terminate_error = terminate_error
        self.terminated = False

    @property
    def pid(self) -> int:
        """Return a stable process identifier."""
        return 42

    @property
    def returncode(self) -> int | None:
        """Return the mutable exit code."""
        return self.code

    async def terminate_tree(self) -> None:
        """Record termination or simulate an OS cleanup failure."""
        self.terminated = True
        if self.terminate_error:
            raise OSError("cleanup")
        self.code = 0


class FakeLauncher:
    """Record launches and optionally emit the redirector ready marker."""

    def __init__(self, process: FakeProcess, *, ready: bool = True, error: bool = False) -> None:
        """Configure the launch response."""
        self.process = process
        self.ready = ready
        self.error = error
        self.arguments: tuple[str, ...] = ()
        self.environment: Mapping[str, str] = {}

    async def launch(
        self,
        executable: Path,
        *,
        arguments: tuple[str, ...],
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> RunningProcess:
        """Return the fake process after recording the safe launch vector."""
        del executable, working_directory
        if self.error:
            raise OSError("launch")
        self.arguments = arguments
        self.environment = environment
        if self.ready:
            preloader = Path(arguments[arguments.index("--doorstop-target") + 1])
            (preloader.parents[1] / "LogOutput.log").write_text(
                "[MODDEBUGPILOT] save_redirect_ready\n",
                encoding="utf-8",
            )
        return self.process


class FakeCapturer:
    """Write one deterministic screenshot."""

    async def capture(self, destination: Path) -> None:
        """Create the requested capture path."""
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"png")


class FakeProbe:
    """Return one configured normal-game state."""

    def __init__(self, *, running: bool = False) -> None:
        """Store the probe result."""
        self.running = running

    async def image_is_running(self, image_name: str) -> bool:
        """Return whether the exact game image is active."""
        assert image_name == "Lethal Company.exe"
        return self.running


def make_config(tmp_path: Path) -> AgentRuntimeConfig:
    """Create separated game, Agent, artifact, and normal-save roots."""
    game = tmp_path / "game" / "Lethal Company.exe"
    game.parent.mkdir(parents=True)
    game.write_bytes(b"exe")
    return AgentRuntimeConfig(
        game_executable=game,
        data_root=tmp_path / "agent-data",
        artifact_root=tmp_path / "artifacts",
        save_directory=tmp_path / "normal-saves",
        max_upload_bytes=8 * 1024 * 1024,
    )


def make_profile(root: Path, *, bootstrap: bytes = b"loader") -> None:
    """Create the minimum complete profile accepted by the runtime."""
    core = root / "BepInEx" / "core"
    core.mkdir(parents=True)
    (core / "BepInEx.Preloader.dll").write_bytes(b"preloader")
    (root / "winhttp.dll").write_bytes(bootstrap)
    (root / "doorstop_config.ini").write_bytes(bootstrap)


def make_runtime(
    tmp_path: Path,
    *,
    process: FakeProcess | None = None,
    ready: bool = True,
    running_normal_game: bool = False,
    launcher_error: bool = False,
) -> tuple[RemoteAgentRuntime, FakeLauncher, AgentRuntimeConfig]:
    """Compose the runtime from deterministic effect adapters."""
    config = make_config(tmp_path)
    selected_process = process or FakeProcess()
    launcher = FakeLauncher(selected_process, ready=ready, error=launcher_error)
    runtime = RemoteAgentRuntime(
        config,
        launcher=launcher,
        capturer=FakeCapturer(),
        process_probe=FakeProbe(running=running_normal_game),
        environment={"PATH": "safe", "SECRET": "excluded"},
        platform_name="win32",
    )
    return runtime, launcher, config


def test_runtime_config_validation(tmp_path: Path) -> None:
    """Configuration rejects non-Windows, missing, overlapping, and invalid limits."""
    config = make_config(tmp_path)
    config.validate(platform_name="win32")
    with pytest.raises(AgentRuntimeError, match="Windows"):
        config.validate(platform_name="linux")
    config.game_executable.unlink()
    with pytest.raises(AgentRuntimeError, match="not found"):
        config.validate(platform_name="win32")
    config.game_executable.write_bytes(b"exe")
    for invalid in (
        replace(config, max_upload_bytes=1),
        replace(config, max_upload_bytes=3 * 1024**3),
        replace(config, data_root=config.game_executable.parent),
        replace(config, save_directory=config.data_root),
    ):
        with pytest.raises(AgentRuntimeError):
            invalid.validate(platform_name="win32")


def test_save_isolation_round_trip_and_no_original(tmp_path: Path) -> None:
    """Normal saves return byte-for-byte while debug saves are archived."""
    save = tmp_path / "saves"
    save.mkdir()
    (save / "LCGeneralSaveData").write_bytes(b"normal")
    isolation = SaveIsolation(save_directory=save, state_root=tmp_path / "state")
    isolation.activate()
    isolation.activate()
    assert isolation.active
    (save / "LCSaveFile1").write_bytes(b"debug")
    isolation.restore()
    assert (save / "LCGeneralSaveData").read_bytes() == b"normal"
    assert next((tmp_path / "state/debug-saves").iterdir()).joinpath("LCSaveFile1").is_file()
    isolation.restore()

    empty_save = tmp_path / "new-saves"
    empty = SaveIsolation(save_directory=empty_save, state_root=tmp_path / "new-state")
    empty.activate()
    empty.restore()
    assert not empty_save.exists()


def test_save_isolation_recovery_and_fail_closed_cases(tmp_path: Path) -> None:
    """Crash journals recover, while ambiguous backups and journals stop safely."""
    save = tmp_path / "saves"
    save.mkdir()
    (save / "normal").write_bytes(b"n")
    state = tmp_path / "state"
    first = SaveIsolation(save_directory=save, state_root=state)
    first.activate()
    SaveIsolation(save_directory=save, state_root=state).recover()
    assert (save / "normal").is_file()
    SaveIsolation(save_directory=save, state_root=state).recover()

    journal = state / "save-isolation.json"
    state.mkdir(exist_ok=True)
    for payload in ("bad", "{}", '{"original_existed":"yes","session_id":1}'):
        journal.write_text(payload, encoding="utf-8")
        with pytest.raises(AgentRuntimeError, match="journal"):
            SaveIsolation(save_directory=save, state_root=state).recover()
    journal.unlink()

    backup = save.with_name(save.name + ".moddebugpilot-normal")
    backup.mkdir()
    with pytest.raises(AgentRuntimeError, match="backup already"):
        SaveIsolation(save_directory=save, state_root=state).activate()
    backup.rmdir()

    original_mkdir = Path.mkdir

    def fail_debug_directory(path: Path, *args: object, **kwargs: object) -> None:
        if path == save:
            raise OSError("mkdir failed")  # noqa: TRY003 - stable test fixture
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch.object(Path, "mkdir", fail_debug_directory),
        pytest.raises(OSError, match="mkdir"),
    ):
        SaveIsolation(save_directory=save, state_root=state).activate()
    assert (save / "normal").is_file()


def test_save_restore_rejects_missing_unexpected_and_colliding_data(tmp_path: Path) -> None:
    """Recovery never overwrites an absent backup or existing debug archive."""
    save = tmp_path / "saves"
    state = tmp_path / "state"
    state.mkdir()
    journal = state / "save-isolation.json"
    journal.write_text(
        json.dumps({"original_existed": True, "session_id": "missing"}), encoding="utf-8"
    )
    with pytest.raises(AgentRuntimeError, match="missing"):
        SaveIsolation(save_directory=save, state_root=state).recover()

    journal.write_text(
        json.dumps({"original_existed": False, "session_id": "unexpected"}), encoding="utf-8"
    )
    save.with_name(save.name + ".moddebugpilot-normal").mkdir()
    with pytest.raises(AgentRuntimeError, match="Unexpected"):
        SaveIsolation(save_directory=save, state_root=state).recover()

    save.mkdir()
    (state / "debug-saves/collision").mkdir(parents=True)
    journal.write_text(
        json.dumps({"original_existed": False, "session_id": "collision"}), encoding="utf-8"
    )
    with pytest.raises(AgentRuntimeError, match="collision"):
        SaveIsolation(save_directory=save, state_root=state).recover()


def test_bootstrap_isolation_round_trip_recovery_and_compatibility(tmp_path: Path) -> None:
    """Doorstop files are journaled, shared only when identical, and restored."""
    game = tmp_path / "game"
    game.mkdir()
    (game / "winhttp.dll").write_bytes(b"original")
    (game / "doorstop_config.ini").write_bytes(b"original-config")
    profile = tmp_path / "profile"
    make_profile(profile)
    state = tmp_path / "state"
    isolation = BootstrapIsolation(game_directory=game, state_root=state)
    isolation.activate(profile)
    isolation.activate(profile)
    assert (game / "winhttp.dll").read_bytes() == b"loader"
    other = tmp_path / "other"
    make_profile(other, bootstrap=b"different")
    with pytest.raises(AgentRuntimeError, match="identical"):
        isolation.activate(other)
    isolation.restore()
    assert (game / "winhttp.dll").read_bytes() == b"original"
    assert (game / "doorstop_config.ini").read_bytes() == b"original-config"
    isolation.restore()

    isolation.activate(profile)
    BootstrapIsolation(game_directory=game, state_root=state).recover()
    assert (game / "winhttp.dll").read_bytes() == b"original"


def test_bootstrap_rejects_backup_and_rolls_back_copy_failure(tmp_path: Path) -> None:
    """Existing backups and partial bootstrap copies cannot corrupt the game."""
    game = tmp_path / "game"
    game.mkdir()
    profile = tmp_path / "profile"
    make_profile(profile)
    state = tmp_path / "state"
    (state / "bootstrap-backup").mkdir(parents=True)
    with pytest.raises(AgentRuntimeError, match="backup already"):
        BootstrapIsolation(game_directory=game, state_root=state).activate(profile)
    (state / "bootstrap-backup").rmdir()
    original_copy = shutil.copy2

    def fail_second_copy(source: Path, destination: Path) -> Path:
        if source.name == "doorstop_config.ini":
            raise OSError("copy failed")  # noqa: TRY003 - stable test fixture
        return cast(Path, original_copy(source, destination))

    with (
        patch("mod_debug_pilot.infrastructure.agent_runtime.shutil.copy2", fail_second_copy),
        pytest.raises(OSError, match="copy"),
    ):
        BootstrapIsolation(game_directory=game, state_root=state).activate(profile)
    assert not (game / "winhttp.dll").exists()
    (state / "bootstrap-isolation.json").write_text("{}", encoding="utf-8")
    BootstrapIsolation(game_directory=game, state_root=state).recover()


def test_tasklist_probe_success_and_failure() -> None:
    """The Windows probe parses exact CSV image names and reports command failure."""

    async def run() -> None:
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(b'"Lethal Company.exe","42"\r\n', b""))
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            assert await TasklistProcessProbe().image_is_running("Lethal Company.exe")
            assert not await TasklistProcessProbe().image_is_running("Other.exe")
        process.returncode = 1
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
            pytest.raises(AgentRuntimeError, match="inspect"),
        ):
            await TasklistProcessProbe().image_is_running("Lethal Company.exe")

    asyncio.run(run())


def test_runtime_install_launch_capture_stop_and_artifact(tmp_path: Path) -> None:
    """A complete installed profile runs with injected saves and exact artifacts."""

    async def run() -> None:
        runtime, launcher, config = make_runtime(tmp_path)
        draft = tmp_path / "draft"
        make_profile(draft)
        bundle = tmp_path / "profile.mdp-profile"
        ProfileWorkspace.create_bundle(
            draft, profile_name="Profile", source_mods=(), destination=bundle
        )
        assert await runtime.install_profile("profile-1", bundle.read_bytes()) == "Profile"
        snapshot = await runtime.launch(InstanceSpec(name="host", profile_id="profile-1"))
        assert snapshot.status is InstanceStatus.RUNNING
        assert "MODDEBUGPILOT_SAVE_ROOT" in launcher.environment
        assert "SECRET" not in launcher.environment
        assert "--doorstop-target" in launcher.arguments
        capture = await runtime.capture(snapshot.instance_id)
        assert runtime.artifact(
            snapshot.instance_id, capture.relative_to(capture.parents[1]).as_posix()
        )
        stopped = await runtime.stop(snapshot.instance_id)
        assert stopped.status is InstanceStatus.STOPPED
        assert await runtime.list_instances() == (stopped,)
        assert not config.save_directory.exists()
        await runtime.shutdown()

    asyncio.run(run())


def test_runtime_install_and_lookup_rejections(tmp_path: Path) -> None:
    """Uploads and lookups reject oversize, unsafe, duplicate, and missing inputs."""

    async def run() -> None:
        runtime, _, config = make_runtime(tmp_path)
        with pytest.raises(ProfileImportError, match="large"):
            await runtime.install_profile("p", b"x" * (config.max_upload_bytes + 1))
        with pytest.raises(ProfileImportError, match="identifier"):
            await runtime.install_profile("../p", b"x")
        with pytest.raises(AgentRuntimeError, match="installed"):
            await runtime.launch(InstanceSpec(name="x", profile_id="missing"))
        for operation in (runtime.stop, runtime.capture):
            with pytest.raises(AgentRuntimeError, match="not found"):
                await operation("missing")
        with pytest.raises(AgentRuntimeError, match="not found"):
            runtime.artifact("missing", "x")

    asyncio.run(run())


def test_runtime_normal_game_port_and_launch_failure(tmp_path: Path) -> None:
    """External games, debugger collisions, and failed launches roll back isolation."""

    async def run() -> None:
        runtime, _, config = make_runtime(tmp_path / "normal", running_normal_game=True)
        make_profile(config.data_root / "profiles/p")
        with pytest.raises(AgentRuntimeError, match="outside"):
            await runtime.launch(InstanceSpec(name="x", profile_id="p"))

        runtime, _, config = make_runtime(tmp_path / "fail", launcher_error=True)
        make_profile(config.data_root / "profiles/p")
        with pytest.raises(OSError, match="launch"):
            await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        assert not config.save_directory.exists()

        runtime, _, config = make_runtime(tmp_path / "ports")
        make_profile(config.data_root / "profiles/p")
        first = await runtime.launch(InstanceSpec(name="x", profile_id="p", debugger_port=55555))
        with pytest.raises(AgentRuntimeError, match="port"):
            await runtime.launch(InstanceSpec(name="y", profile_id="p", debugger_port=55555))
        make_profile(config.data_root / "profiles/q", bootstrap=b"different")
        with pytest.raises(AgentRuntimeError, match="identical"):
            await runtime.launch(InstanceSpec(name="z", profile_id="q", debugger_port=55557))
        second = await runtime.launch(InstanceSpec(name="y", profile_id="p", debugger_port=55556))
        assert len(await runtime.list_instances()) == len((first, second))
        await runtime.stop(second.instance_id)
        await runtime.stop(second.instance_id)
        await runtime.stop(first.instance_id)

    asyncio.run(run())


def test_runtime_redirect_failures_and_timeout(tmp_path: Path) -> None:
    """Exited or silent redirectors are terminated and their transactions restored."""

    async def run() -> None:
        exited = FakeProcess(returncode=86)
        runtime, _, config = make_runtime(tmp_path / "exited", process=exited, ready=False)
        make_profile(config.data_root / "profiles/p")
        with pytest.raises(AgentRuntimeError, match="exited"):
            await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        assert exited.terminated

        silent = FakeProcess()
        runtime, _, config = make_runtime(tmp_path / "silent", process=silent, ready=False)
        make_profile(config.data_root / "profiles/p")
        with (
            patch(
                "mod_debug_pilot.infrastructure.agent_runtime._SAVE_REDIRECT_TIMEOUT_SECONDS",
                0.0,
            ),
            pytest.raises(AgentRuntimeError, match="did not become ready"),
        ):
            await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        assert silent.terminated

        delayed = FakeProcess()
        runtime, _, config = make_runtime(tmp_path / "delayed", process=delayed, ready=False)
        make_profile(config.data_root / "profiles/p")
        with (
            patch(
                "mod_debug_pilot.infrastructure.agent_runtime._file_contains",
                side_effect=[False, True],
            ),
            patch("mod_debug_pilot.infrastructure.agent_runtime.asyncio.sleep", AsyncMock()),
        ):
            launched = await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        await runtime.stop(launched.instance_id)

    asyncio.run(run())


def test_runtime_natural_exit_shutdown_and_watcher_paths(tmp_path: Path) -> None:
    """Natural failure, cleanup failure, watcher absence, and cancellation are recorded."""

    async def run() -> None:
        process = FakeProcess()
        runtime, _, config = make_runtime(tmp_path / "natural", process=process)
        make_profile(config.data_root / "profiles/p")
        snapshot = await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        process.code = 7
        listed = await runtime.list_instances()
        assert listed[0].status is InstanceStatus.FAILED
        log = config.artifact_root / snapshot.instance_id / "profile/BepInEx/LogOutput.log"
        assert log.is_file()

        process = FakeProcess(terminate_error=True)
        runtime, _, config = make_runtime(tmp_path / "shutdown", process=process)
        make_profile(config.data_root / "profiles/p")
        await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        await runtime.shutdown()
        assert (await runtime.list_instances())[0].status is InstanceStatus.FAILED

        process = FakeProcess()
        runtime, _, config = make_runtime(tmp_path / "shutdown-success", process=process)
        make_profile(config.data_root / "profiles/p")
        running = await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        (config.artifact_root / running.instance_id / "profile/BepInEx/LogOutput.log").unlink()
        await runtime.shutdown()
        assert (await runtime.list_instances())[0].status is InstanceStatus.STOPPED

        runtime, _, _ = make_runtime(tmp_path / "watch")
        with patch("mod_debug_pilot.infrastructure.agent_runtime.asyncio.sleep", AsyncMock()):
            await runtime._watch_instance("missing")  # noqa: SLF001
        with patch(
            "mod_debug_pilot.infrastructure.agent_runtime.asyncio.sleep",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            await runtime._watch_instance("missing")  # noqa: SLF001

        process = FakeProcess()
        runtime, _, config = make_runtime(tmp_path / "watch-exit", process=process)
        make_profile(config.data_root / "profiles/p")
        snapshot = await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        runtime._watchers[snapshot.instance_id].cancel()  # noqa: SLF001
        count = 0

        async def exit_after_poll(_seconds: float) -> None:
            nonlocal count
            count += 1
            if count == len((False, True)):
                process.code = 0

        with patch(
            "mod_debug_pilot.infrastructure.agent_runtime.asyncio.sleep",
            side_effect=exit_after_poll,
        ):
            await runtime._watch_instance(snapshot.instance_id)  # noqa: SLF001

    asyncio.run(run())


def test_runtime_helpers_and_system_default(tmp_path: Path) -> None:
    """Identifier, confinement, marker, and production composition helpers are bounded."""
    assert _safe_identifier("profile-1")
    assert not _safe_identifier("")
    assert not _safe_identifier("x" * 65)
    assert not _safe_identifier("bad/name")
    root = tmp_path / "root"
    root.mkdir()
    assert _confined(root, "a.txt") == root / "a.txt"
    for relative in ("", "../x"):
        with pytest.raises(AgentRuntimeError):
            _confined(root, relative)
    marker = root / "log"
    assert not _file_contains(marker, "x")
    marker.write_text("hello x", encoding="utf-8")
    assert _file_contains(marker, "x")
    with patch.object(Path, "read_text", side_effect=OSError):
        assert not _file_contains(marker, "x")
    config = make_config(tmp_path / "default")
    with patch("mod_debug_pilot.infrastructure.agent_runtime.sys.platform", "win32"):
        assert isinstance(RemoteAgentRuntime.system_default(config), RemoteAgentRuntime)


def test_runtime_recover_delegates_transactions(tmp_path: Path) -> None:
    """Startup runs both persisted transaction recovery paths."""

    async def run() -> None:
        runtime, _, _ = make_runtime(tmp_path)
        runtime._bootstrap.recover = Mock()  # type: ignore[method-assign]  # noqa: SLF001
        runtime._save.recover = Mock()  # type: ignore[method-assign]  # noqa: SLF001
        await runtime.recover()
        runtime._bootstrap.recover.assert_called_once()  # noqa: SLF001
        runtime._save.recover.assert_called_once()  # noqa: SLF001

    asyncio.run(run())


def test_runtime_none_process_and_missing_artifact(tmp_path: Path) -> None:
    """A malformed launcher and missing regular artifact still fail closed."""

    class NoneLauncher:
        async def launch(self, *_args: object, **_kwargs: object) -> RunningProcess:
            return cast(RunningProcess, None)

    async def run() -> None:
        config = make_config(tmp_path)
        make_profile(config.data_root / "profiles/p")
        runtime = RemoteAgentRuntime(
            config,
            launcher=NoneLauncher(),
            capturer=FakeCapturer(),
            process_probe=FakeProbe(),
            environment={},
            platform_name="win32",
        )
        with pytest.raises(AgentRuntimeError, match="not created"):
            await runtime.launch(InstanceSpec(name="x", profile_id="p"))

        runtime, _, config = make_runtime(tmp_path / "artifact")
        make_profile(config.data_root / "profiles/p")
        snapshot = await runtime.launch(InstanceSpec(name="x", profile_id="p"))
        with pytest.raises(AgentRuntimeError, match="Artifact"):
            runtime.artifact(snapshot.instance_id, "missing")
        await runtime.stop(snapshot.instance_id)

    asyncio.run(run())
