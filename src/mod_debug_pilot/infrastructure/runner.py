"""Trusted local runner for BepInEx smoke-test jobs."""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from PIL import ImageGrab

from mod_debug_pilot import __version__
from mod_debug_pilot.domain import JobKind, JobOutcome, JobRequest, JobResult, PilotConfig
from mod_debug_pilot.infrastructure.settings import write_json_atomic

_POLL_SECONDS = 0.2
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_GAME_ENVIRONMENT_KEYS = frozenset(
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
    },
)


class UnsafeJobIdentifierError(OSError):
    """Reject an artifact identifier that could escape its configured root."""


class UnsafeArtifactPathError(OSError):
    """Reject artifacts written inside game or base-profile input trees."""


class RunningProcess(Protocol):
    """Minimal process surface needed by the runner."""

    @property
    def pid(self) -> int:
        """Return the operating-system process identifier."""
        ...

    @property
    def returncode(self) -> int | None:
        """Return the exit status when available."""
        ...

    async def terminate_tree(self) -> None:
        """Terminate the full launched process tree."""
        ...


class ProcessLauncher(Protocol):
    """Start a process without a shell."""

    async def launch(
        self,
        executable: Path,
        *,
        arguments: tuple[str, ...],
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> RunningProcess:
        """Launch one process with an explicit argument vector."""
        ...


class ScreenCapturer(Protocol):
    """Capture a diagnostic image."""

    async def capture(self, destination: Path) -> None:
        """Capture the primary display into a PNG file."""
        ...


class RuntimeClock(Protocol):
    """Provide deterministic time and sleeps."""

    def now(self) -> datetime:
        """Return current UTC time."""
        ...

    def monotonic(self) -> float:
        """Return monotonic seconds."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Sleep without blocking the event loop."""
        ...


class SystemClock:
    """Use operating-system UTC and monotonic clocks."""

    def now(self) -> datetime:
        """Return current UTC time."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Return monotonic seconds."""
        return monotonic()

    async def sleep(self, seconds: float) -> None:
        """Sleep asynchronously."""
        await asyncio.sleep(seconds)


@dataclass(slots=True)
class AsyncioRunningProcess:
    """Adapt an asyncio subprocess to the trusted process contract."""

    process: asyncio.subprocess.Process

    @property
    def pid(self) -> int:
        """Return the process identifier."""
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        """Return the exit status when available."""
        return self.process.returncode

    async def terminate_tree(self) -> None:
        """Terminate descendants on Windows, with a bounded direct fallback."""
        if self.process.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(self.process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=3)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


class AsyncioProcessLauncher:
    """Launch the game directly without shell interpretation."""

    async def launch(
        self,
        executable: Path,
        *,
        arguments: tuple[str, ...],
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> RunningProcess:
        """Launch and suppress inherited console streams."""
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *arguments,
            cwd=working_directory,
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return AsyncioRunningProcess(process)


class PillowScreenCapturer:
    """Capture the primary display through Pillow."""

    async def capture(self, destination: Path) -> None:
        """Capture and save outside the UI event loop."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._capture_sync, destination)

    @staticmethod
    def _capture_sync(destination: Path) -> None:
        image = ImageGrab.grab(all_screens=False)
        image.save(destination, format="PNG")


class LocalJobExecutor:
    """Validate and execute local allow-listed jobs with recoverable artifacts."""

    def __init__(
        self,
        *,
        launcher: ProcessLauncher,
        capturer: ScreenCapturer,
        clock: RuntimeClock,
        platform_name: str,
        environment: Mapping[str, str],
    ) -> None:
        """Create an executor with explicit operating-system effects."""
        self._launcher = launcher
        self._capturer = capturer
        self._clock = clock
        self._platform_name = platform_name
        self._environment = dict(environment)

    @classmethod
    def system_default(cls) -> LocalJobExecutor:
        """Create the production Windows adapter set."""
        return cls(
            launcher=AsyncioProcessLauncher(),
            capturer=PillowScreenCapturer(),
            clock=SystemClock(),
            platform_name=sys.platform,
            environment=os.environ,
        )

    async def execute(self, request: JobRequest) -> JobResult:
        """Execute validation or one smoke test and always write a result."""
        started = self._clock.now()
        artifact_dir = self._artifact_dir(request)
        artifact_dir.mkdir(parents=True, exist_ok=False)
        await asyncio.to_thread(
            write_json_atomic,
            artifact_dir / "request.json",
            request.to_mapping(),
        )
        await asyncio.to_thread(
            write_json_atomic,
            artifact_dir / "environment.json",
            self._environment_payload(request.config),
        )
        try:
            issues = self._validation_issues(request.config)
            if issues:
                result = self._result(
                    request,
                    outcome=JobOutcome.FAILED,
                    message="Environment validation failed: " + " ".join(issues),
                    artifact_dir=artifact_dir,
                    started=started,
                )
            elif request.kind is JobKind.VALIDATE_ENVIRONMENT:
                result = self._result(
                    request,
                    outcome=JobOutcome.SUCCEEDED,
                    message="Environment validation passed.",
                    artifact_dir=artifact_dir,
                    started=started,
                )
            else:
                result = await self._run_smoke(request, artifact_dir=artifact_dir, started=started)
        except asyncio.CancelledError:
            canceled = self._result(
                request,
                outcome=JobOutcome.CANCELED,
                message="Job canceled; partial artifacts were preserved.",
                artifact_dir=artifact_dir,
                started=started,
            )
            await asyncio.to_thread(
                write_json_atomic,
                artifact_dir / "result.json",
                canceled.to_mapping(),
            )
            raise
        except OSError:
            result = self._result(
                request,
                outcome=JobOutcome.FAILED,
                message="A local file or process operation failed.",
                artifact_dir=artifact_dir,
                started=started,
            )
        await asyncio.to_thread(
            write_json_atomic,
            artifact_dir / "result.json",
            result.to_mapping(),
        )
        return result

    def _validation_issues(self, config: PilotConfig) -> list[str]:
        checks = (
            (self._platform_name == "win32", "Windows is required."),
            (Path(config.game_executable).is_file(), "Game executable was not found."),
            (Path(config.base_profile_dir).is_dir(), "Base profile was not found."),
            (Path(config.mod_dll).is_file(), "Mod DLL was not found."),
            (
                (Path(config.base_profile_dir) / "winhttp.dll").is_file(),
                "Base profile has no winhttp.dll.",
            ),
            (
                (Path(config.base_profile_dir) / "doorstop_config.ini").is_file(),
                "Base profile has no doorstop_config.ini.",
            ),
            (
                (
                    Path(config.base_profile_dir) / "BepInEx" / "core" / "BepInEx.Preloader.dll"
                ).is_file(),
                "Base profile has no BepInEx preloader.",
            ),
        )
        return [message for passed, message in checks if not passed]

    async def _run_smoke(
        self,
        request: JobRequest,
        *,
        artifact_dir: Path,
        started: datetime,
    ) -> JobResult:
        config = request.config
        profile_dir = artifact_dir / "profile"
        await asyncio.to_thread(shutil.copytree, config.base_profile_dir, profile_dir)
        plugin_dir = profile_dir / "BepInEx" / "plugins" / "ModDebugPilot"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            shutil.copy2,
            config.mod_dll,
            plugin_dir / Path(config.mod_dll).name,
        )
        game_dir = Path(config.game_executable).parent
        bootstrap = _BootstrapGuard(
            game_dir=game_dir,
            profile_dir=profile_dir,
            job_id=request.job_id,
        )
        process: RunningProcess | None = None
        try:
            await asyncio.to_thread(bootstrap.install)
            preloader = profile_dir / "BepInEx" / "core" / "BepInEx.Preloader.dll"
            arguments = (
                "-screen-width",
                str(config.screen_width),
                "-screen-height",
                str(config.screen_height),
                "--doorstop-enable",
                "true",
                "--doorstop-target",
                str(preloader),
            )
            environment = {
                key: value
                for key, value in self._environment.items()
                if key.upper() in _GAME_ENVIRONMENT_KEYS
            }
            environment["MONO_ENV_OPTIONS"] = (
                "--debugger-agent=transport=dt_socket,server=y,"
                f"address=127.0.0.1:{config.debugger_port},embedding=1,defer=y"
            )
            process = await self._launcher.launch(
                Path(config.game_executable),
                arguments=arguments,
                working_directory=game_dir,
                environment=environment,
            )
            outcome, message = await self._wait_for_ready(
                process,
                config=config,
                profile_dir=profile_dir,
                artifact_dir=artifact_dir,
            )
        finally:
            try:
                if process is not None:
                    await process.terminate_tree()
            finally:
                await asyncio.to_thread(bootstrap.restore)
                log_path = profile_dir / "BepInEx" / "LogOutput.log"
                if log_path.is_file():
                    await asyncio.to_thread(shutil.copy2, log_path, artifact_dir / "game.log")
        return self._result(
            request,
            outcome=outcome,
            message=message,
            artifact_dir=artifact_dir,
            started=started,
        )

    async def _wait_for_ready(
        self,
        process: RunningProcess,
        *,
        config: PilotConfig,
        profile_dir: Path,
        artifact_dir: Path,
    ) -> tuple[JobOutcome, str]:
        deadline = self._clock.monotonic() + config.timeout_seconds
        log_path = profile_dir / "BepInEx" / "LogOutput.log"
        while self._clock.monotonic() < deadline:
            if process.returncode is not None:
                return JobOutcome.FAILED, "Game exited before the ready marker appeared."
            if await asyncio.to_thread(_file_contains, log_path, config.ready_marker):
                await self._clock.sleep(config.screenshot_delay_seconds)
                await self._capturer.capture(artifact_dir / "screenshots" / "ready.png")
                return JobOutcome.SUCCEEDED, "Smoke test reached the ready marker."
            await self._clock.sleep(_POLL_SECONDS)
        return JobOutcome.TIMED_OUT, "Smoke test timed out before the ready marker appeared."

    def _result(
        self,
        request: JobRequest,
        *,
        outcome: JobOutcome,
        message: str,
        artifact_dir: Path,
        started: datetime,
    ) -> JobResult:
        return JobResult(
            job_id=request.job_id,
            outcome=outcome,
            message=message,
            artifact_dir=str(artifact_dir),
            started_at=started.isoformat(),
            finished_at=self._clock.now().isoformat(),
        )

    @staticmethod
    def _artifact_dir(request: JobRequest) -> Path:
        if _JOB_ID_PATTERN.fullmatch(request.job_id) is None:
            raise UnsafeJobIdentifierError
        root = Path(request.config.artifact_root).resolve()
        game_dir = Path(request.config.game_executable).parent.resolve()
        base_profile = Path(request.config.base_profile_dir).resolve()
        if _is_within(root, game_dir) or _is_within(root, base_profile):
            raise UnsafeArtifactPathError
        return root / request.job_id

    def _environment_payload(self, config: PilotConfig) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mod_debug_pilot": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "screen": {"width": config.screen_width, "height": config.screen_height},
            "profile_name": config.profile_name,
        }


@dataclass(slots=True, kw_only=True)
class _BootstrapGuard:
    """Install and restore the two Doorstop files managed by a run."""

    game_dir: Path
    profile_dir: Path
    job_id: str
    _active: bool = field(default=False, init=False)

    @property
    def backup_dir(self) -> Path:
        """Return the unique backup path beside the game executable."""
        return self.game_dir / f".moddebugpilot-backup-{self.job_id}"

    def install(self) -> None:
        """Back up existing files and install the profile bootstrap."""
        if self.backup_dir.exists():
            raise OSError
        self.backup_dir.mkdir()
        self._active = True
        try:
            for name in ("winhttp.dll", "doorstop_config.ini"):
                target = self.game_dir / name
                if target.exists():
                    shutil.move(target, self.backup_dir / name)
                shutil.copy2(self.profile_dir / name, target)
        except BaseException:
            self.restore()
            raise

    def restore(self) -> None:
        """Remove managed files and restore any originals."""
        if not self._active:
            return
        for name in ("winhttp.dll", "doorstop_config.ini"):
            target = self.game_dir / name
            target.unlink(missing_ok=True)
            backup = self.backup_dir / name
            if backup.exists():
                shutil.move(backup, target)
        self.backup_dir.rmdir()
        self._active = False


def _file_contains(path: Path, marker: str) -> bool:
    if not path.is_file():
        return False
    try:
        return marker in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents
