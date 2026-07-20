"""Tests for the trusted local process, capture, and artifact runner."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Awaitable, Coroutine, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from mod_debug_pilot.domain import JobKind, JobOutcome, JobRequest, PilotConfig
from mod_debug_pilot.infrastructure.runner import (
    AsyncioProcessLauncher,
    AsyncioRunningProcess,
    LocalJobExecutor,
    PillowScreenCapturer,
    RunningProcess,
    RuntimeClock,
    ScreenCapturer,
    SystemClock,
    UnsafeArtifactPathError,
    UnsafeJobIdentifierError,
)


class FakeProcess:
    """Controllable game-process adapter."""

    def __init__(
        self,
        *,
        returncode: int | None = None,
        terminate_error: OSError | None = None,
    ) -> None:
        """Create an active or exited process."""
        self._returncode = returncode
        self.terminate_error = terminate_error
        self.terminated = False

    @property
    def pid(self) -> int:
        """Return a stable fake identifier."""
        return 42

    @property
    def returncode(self) -> int | None:
        """Return the configured exit status."""
        return self._returncode

    async def terminate_tree(self) -> None:
        """Record cleanup."""
        self.terminated = True
        if self.terminate_error is not None:
            raise self.terminate_error


class FakeLauncher:
    """Record a launch or raise a configured error."""

    def __init__(self, *, process: FakeProcess, error: OSError | None = None) -> None:
        """Configure launch behavior."""
        self.process = process
        self.error = error
        self.arguments: tuple[str, ...] | None = None
        self.environment: Mapping[str, str] | None = None

    async def launch(
        self,
        *,
        executable: Path,
        arguments: tuple[str, ...],
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> RunningProcess:
        """Record the safe argument vector and return the fake process."""
        del executable, working_directory
        if self.error is not None:
            raise self.error
        self.arguments = arguments
        self.environment = environment
        return self.process


class FakeClock:
    """Advance deterministic time when sleeps occur."""

    def __init__(self) -> None:
        """Start at one fixed UTC instant and zero monotonic time."""
        self.current = datetime(2026, 7, 20, tzinfo=UTC)
        self.elapsed = 0.0

    def now(self) -> datetime:
        """Return deterministic UTC time."""
        return self.current + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        """Return deterministic elapsed seconds."""
        return self.elapsed

    async def sleep(self, *, seconds: float) -> None:
        """Advance time and yield to cancellation."""
        self.elapsed += seconds
        await asyncio.sleep(0)


class FakeCapturer:
    """Write a deterministic PNG stand-in."""

    def __init__(self) -> None:
        """Create a capture recorder."""
        self.destination: Path | None = None

    async def capture(self, *, destination: Path) -> None:
        """Write a small deterministic artifact."""
        self.destination = destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"png")


class BlockingCapturer:
    """Expose a cancellation point after the ready marker."""

    def __init__(self) -> None:
        """Create the started signal."""
        self.started = asyncio.Event()

    async def capture(self, *, destination: Path) -> None:
        """Block forever after recording entry."""
        del destination
        self.started.set()
        await asyncio.Event().wait()


def make_config(*, tmp_path: Path, marker_in_log: bool = False) -> PilotConfig:
    """Create a complete local BepInEx profile fixture."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    game_executable = game_dir / "Lethal Company.exe"
    game_executable.write_bytes(b"exe")
    base = tmp_path / "base"
    core = base / "BepInEx" / "core"
    core.mkdir(parents=True)
    (core / "BepInEx.Preloader.dll").write_bytes(b"preloader")
    (base / "winhttp.dll").write_bytes(b"loader")
    (base / "doorstop_config.ini").write_text("enabled=true", encoding="utf-8")
    if marker_in_log:
        (base / "BepInEx" / "LogOutput.log").write_text("READY", encoding="utf-8")
    mod_dll = tmp_path / "Example.dll"
    mod_dll.write_bytes(b"mod")
    return PilotConfig(
        game_executable=str(game_executable),
        base_profile_dir=str(base),
        mod_dll=str(mod_dll),
        artifact_root=str(tmp_path / "artifacts"),
        ready_marker="READY",
        timeout_seconds=5,
        screenshot_delay_seconds=0,
    )


def make_request(*, config: PilotConfig, kind: JobKind, job_id: str) -> JobRequest:
    """Create a deterministic local request."""
    return JobRequest(job_id=job_id, kind=kind, created_at="created", config=config)


def make_executor(
    *,
    launcher: FakeLauncher,
    capturer: ScreenCapturer | None = None,
    clock: RuntimeClock | None = None,
    platform_name: str = "win32",
) -> LocalJobExecutor:
    """Create an executor with deterministic local adapters."""
    return LocalJobExecutor(
        launcher=launcher,
        capturer=capturer or FakeCapturer(),
        clock=clock or FakeClock(),
        platform_name=platform_name,
        environment={"PUBLIC_SETTING": "value"},
    )


def test_validation_job_records_success_and_every_missing_requirement(*, tmp_path: Path) -> None:
    """Validation produces artifacts without launching and reports all missing inputs."""
    config = make_config(tmp_path=tmp_path)
    launcher = FakeLauncher(process=FakeProcess())
    executor = make_executor(launcher=launcher)

    success = asyncio.run(
        executor.execute(
            request=make_request(config=config, kind=JobKind.VALIDATE_ENVIRONMENT, job_id="valid")
        ),
    )
    assert success.outcome is JobOutcome.SUCCEEDED
    assert launcher.arguments is None
    assert json.loads(Path(success.artifact_dir, "result.json").read_text())["outcome"] == (
        "succeeded"
    )

    missing = PilotConfig(
        game_executable=str(tmp_path / "missing-game" / "missing.exe"),
        base_profile_dir=str(tmp_path / "missing-profile"),
        mod_dll=str(tmp_path / "missing.dll"),
        artifact_root=str(tmp_path / "missing-artifacts"),
    )
    failure = asyncio.run(
        make_executor(launcher=launcher, platform_name="linux").execute(
            request=make_request(
                config=missing, kind=JobKind.VALIDATE_ENVIRONMENT, job_id="invalid"
            ),
        ),
    )
    assert failure.outcome is JobOutcome.FAILED
    assert "Windows is required" in failure.message
    assert "BepInEx preloader" in failure.message


def test_smoke_test_prepares_launch_captures_and_restores(*, tmp_path: Path) -> None:
    """A ready marker captures the display and restores pre-existing bootstrap files."""
    config = make_config(tmp_path=tmp_path, marker_in_log=True)
    game_dir = Path(config.game_executable).parent
    (game_dir / "winhttp.dll").write_bytes(b"original")
    launcher = FakeLauncher(process=FakeProcess())
    capturer = FakeCapturer()
    executor = make_executor(launcher=launcher, capturer=capturer)

    result = asyncio.run(
        executor.execute(
            request=make_request(config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="ready")
        ),
    )

    assert result.outcome is JobOutcome.SUCCEEDED
    assert launcher.process.terminated is True
    assert launcher.arguments is not None
    assert launcher.arguments[:4] == ("-screen-width", "1280", "-screen-height", "720")
    assert launcher.environment is not None
    assert "address=127.0.0.1:55555" in launcher.environment["MONO_ENV_OPTIONS"]
    assert "PUBLIC_SETTING" not in launcher.environment
    assert capturer.destination is not None
    assert capturer.destination.is_file()
    assert (Path(result.artifact_dir) / "game.log").is_file()
    assert (game_dir / "winhttp.dll").read_bytes() == b"original"
    assert not (game_dir / "doorstop_config.ini").exists()
    assert not (game_dir / ".moddebugpilot-backup-ready").exists()


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(1, JobOutcome.FAILED), (None, JobOutcome.TIMED_OUT)],
)
def test_smoke_test_reports_early_exit_and_timeout(
    *,
    tmp_path: Path,
    returncode: int | None,
    expected: JobOutcome,
) -> None:
    """Process exit and absent ready markers have distinct outcomes."""
    config = make_config(tmp_path=tmp_path)
    process = FakeProcess(returncode=returncode)

    result = asyncio.run(
        make_executor(launcher=FakeLauncher(process=process)).execute(
            request=make_request(config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="wait"),
        ),
    )

    assert result.outcome is expected
    assert process.terminated is True


def test_smoke_cancellation_writes_result_and_restores(*, tmp_path: Path) -> None:
    """Cancellation is re-raised only after partial result and bootstrap cleanup."""

    async def scenario() -> None:
        config = make_config(tmp_path=tmp_path, marker_in_log=True)
        process = FakeProcess()
        capturer = BlockingCapturer()
        executor = make_executor(launcher=FakeLauncher(process=process), capturer=capturer)
        request = make_request(config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="cancel")
        task = asyncio.create_task(executor.execute(request=request))
        await capturer.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result_path = Path(config.artifact_root) / "cancel" / "result.json"
        assert json.loads(result_path.read_text())["outcome"] == "canceled"
        assert process.terminated is True
        assert not (Path(config.game_executable).parent / "winhttp.dll").exists()

    asyncio.run(scenario())


def test_smoke_maps_launch_failure_and_preserves_foreign_backup(*, tmp_path: Path) -> None:
    """Local process failure is stable and an unowned backup is never modified."""
    config = make_config(tmp_path=tmp_path)
    failure = asyncio.run(
        make_executor(launcher=FakeLauncher(process=FakeProcess(), error=OSError())).execute(
            request=make_request(
                config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="launch-failure"
            ),
        ),
    )
    assert failure.outcome is JobOutcome.FAILED

    backup = Path(config.game_executable).parent / ".moddebugpilot-backup-foreign"
    backup.mkdir()
    sentinel = backup / "sentinel"
    sentinel.write_text("owned elsewhere", encoding="utf-8")
    collision = asyncio.run(
        make_executor(launcher=FakeLauncher(process=FakeProcess())).execute(
            request=make_request(config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="foreign"),
        ),
    )
    assert collision.outcome is JobOutcome.FAILED
    assert sentinel.read_text(encoding="utf-8") == "owned elsewhere"


def test_smoke_restores_bootstrap_after_partial_install_failure(*, tmp_path: Path) -> None:
    """A bootstrap copy failure rolls back files moved earlier in installation."""
    config = make_config(tmp_path=tmp_path)
    game_dir = Path(config.game_executable).parent
    original = game_dir / "winhttp.dll"
    original.write_bytes(b"original")
    real_copy = shutil.copy2

    def fail_doorstop(source: str | Path, destination: str | Path) -> str:  # noqa: PLR0917 -- keyword-only-exception: shutil callback ABI.
        """Fail only the second managed bootstrap copy."""
        if Path(source).name == "doorstop_config.ini":
            raise OSError
        return str(real_copy(source, destination))

    with patch("mod_debug_pilot.infrastructure.runner.shutil.copy2", side_effect=fail_doorstop):
        result = asyncio.run(
            make_executor(launcher=FakeLauncher(process=FakeProcess())).execute(
                request=make_request(config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="partial"),
            ),
        )

    assert result.outcome is JobOutcome.FAILED
    assert original.read_bytes() == b"original"
    assert not (game_dir / "doorstop_config.ini").exists()


def test_smoke_restores_bootstrap_when_process_cleanup_fails(*, tmp_path: Path) -> None:
    """Doorstop restoration runs even when process-tree termination fails."""
    config = make_config(tmp_path=tmp_path, marker_in_log=True)
    game_dir = Path(config.game_executable).parent
    original = game_dir / "winhttp.dll"
    original.write_bytes(b"original")
    process = FakeProcess(terminate_error=OSError())

    result = asyncio.run(
        make_executor(launcher=FakeLauncher(process=process)).execute(
            request=make_request(config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="cleanup"),
        ),
    )

    assert result.outcome is JobOutcome.FAILED
    assert process.terminated is True
    assert original.read_bytes() == b"original"
    assert not (game_dir / "doorstop_config.ini").exists()


def test_executor_rejects_unsafe_artifact_identity_and_roots(*, tmp_path: Path) -> None:
    """Artifact paths cannot traverse or write inside protected input trees."""
    config = make_config(tmp_path=tmp_path)
    executor = make_executor(launcher=FakeLauncher(process=FakeProcess()))

    with pytest.raises(UnsafeJobIdentifierError):
        asyncio.run(
            executor.execute(
                request=make_request(
                    config=config, kind=JobKind.VALIDATE_ENVIRONMENT, job_id="../escape"
                ),
            ),
        )

    for artifact_root in (
        Path(config.game_executable).parent,
        Path(config.base_profile_dir) / "nested-artifacts",
    ):
        unsafe = PilotConfig.from_mapping(
            values={**config.to_mapping(), "artifact_root": str(artifact_root)},
        )
        with pytest.raises(UnsafeArtifactPathError):
            asyncio.run(
                executor.execute(
                    request=make_request(
                        config=unsafe, kind=JobKind.VALIDATE_ENVIRONMENT, job_id="unsafe"
                    ),
                ),
            )


def test_unreadable_log_is_treated_as_not_ready(*, tmp_path: Path) -> None:
    """A transient log read error remains bounded by the job timeout."""
    config = make_config(tmp_path=tmp_path, marker_in_log=True)
    with patch.object(Path, "read_text", side_effect=OSError):
        result = asyncio.run(
            make_executor(launcher=FakeLauncher(process=FakeProcess())).execute(
                request=make_request(
                    config=config, kind=JobKind.RUN_SMOKE_TEST, job_id="unreadable"
                ),
            ),
        )

    assert result.outcome is JobOutcome.TIMED_OUT


def test_system_clock_and_default_executor() -> None:
    """Production composition supplies real time and concrete adapters."""

    async def scenario() -> None:
        clock = SystemClock()
        assert clock.now().tzinfo is UTC
        first = clock.monotonic()
        await clock.sleep(seconds=0)
        assert clock.monotonic() >= first

    asyncio.run(scenario())
    assert isinstance(LocalJobExecutor.system_default(), LocalJobExecutor)


def test_pillow_capturer_creates_directory_and_png(*, tmp_path: Path) -> None:
    """Pillow capture is moved off-loop and explicitly saved as PNG."""
    image = Mock()
    destination = tmp_path / "nested" / "screen.png"
    with patch("mod_debug_pilot.infrastructure.runner.ImageGrab.grab", return_value=image) as grab:
        asyncio.run(PillowScreenCapturer().capture(destination=destination))

    grab.assert_called_once_with(all_screens=False)
    image.save.assert_called_once_with(destination, format="PNG")


def test_asyncio_launcher_forwards_explicit_process_contract(*, tmp_path: Path) -> None:
    """The production launcher never introduces a shell string."""
    process = Mock(pid=7, returncode=0)
    create = AsyncMock(return_value=process)
    executable = tmp_path / "game.exe"
    with patch("mod_debug_pilot.infrastructure.runner.asyncio.create_subprocess_exec", create):
        result = asyncio.run(
            AsyncioProcessLauncher().launch(
                executable=executable,
                arguments=("--safe", "value"),
                working_directory=tmp_path,
                environment={"PUBLIC": "1"},
            ),
        )

    assert isinstance(result, AsyncioRunningProcess)
    assert result.pid == process.pid
    assert result.returncode == 0
    assert create.await_args is not None
    assert create.await_args.args == (str(executable), "--safe", "value")


def test_asyncio_process_termination_paths() -> None:
    """Exited, Windows-tree, graceful, and forced cleanup paths are bounded."""

    async def scenario() -> None:
        exited = Mock(pid=1, returncode=0)
        await AsyncioRunningProcess(
            process=cast(asyncio.subprocess.Process, exited)
        ).terminate_tree()
        exited.terminate.assert_not_called()

        active_windows = Mock(pid=2, returncode=None)
        killer = Mock()
        killer.wait = AsyncMock(return_value=0)
        create = AsyncMock(return_value=killer)
        with (
            patch.object(os, "name", "nt"),
            patch("mod_debug_pilot.infrastructure.runner.asyncio.create_subprocess_exec", create),
        ):
            await AsyncioRunningProcess(
                process=cast(asyncio.subprocess.Process, active_windows),
            ).terminate_tree()
        assert create.await_args is not None
        assert create.await_args.args[:4] == ("taskkill", "/PID", "2", "/T")

        graceful = Mock(pid=3, returncode=None)
        graceful.wait = AsyncMock(return_value=0)
        with patch.object(os, "name", "posix"):
            await AsyncioRunningProcess(
                process=cast(asyncio.subprocess.Process, graceful),
            ).terminate_tree()
        graceful.terminate.assert_called_once_with()

        forced = Mock(pid=4, returncode=None)
        forced.wait = AsyncMock(return_value=0)

        async def timeout_wait(  # noqa: PLR0917 -- keyword-only-exception: asyncio wait_for callback ABI.
            awaitable: Awaitable[int],
            **options: float,
        ) -> int:
            """Close the intercepted coroutine and simulate bounded expiry."""
            del options
            if isinstance(awaitable, Coroutine):
                awaitable.close()
            raise TimeoutError

        with (
            patch.object(os, "name", "posix"),
            patch(
                "mod_debug_pilot.infrastructure.runner.asyncio.wait_for",
                side_effect=timeout_wait,
            ),
        ):
            await AsyncioRunningProcess(
                process=cast(asyncio.subprocess.Process, forced),
            ).terminate_tree()
        forced.kill.assert_called_once_with()

    asyncio.run(scenario())
