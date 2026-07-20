"""Operating-system adapters used by the tracked Agent runtime."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from PIL import ImageGrab


class RunningProcess(Protocol):
    """Minimal process surface needed by the runtime."""

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
        *,
        executable: Path,
        arguments: tuple[str, ...],
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> RunningProcess:
        """Launch one process with an explicit argument vector."""
        ...


class ScreenCapturer(Protocol):
    """Capture a diagnostic image."""

    async def capture(self, *, destination: Path) -> None:
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

    async def sleep(self, *, seconds: float) -> None:
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

    async def sleep(self, *, seconds: float) -> None:
        """Sleep asynchronously."""
        await asyncio.sleep(seconds)


@dataclass(slots=True, kw_only=True)
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
        *,
        executable: Path,
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
        return AsyncioRunningProcess(process=process)


class PillowScreenCapturer:
    """Capture the primary display through Pillow."""

    async def capture(self, *, destination: Path) -> None:
        """Capture and save outside the UI event loop."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._capture_sync, destination=destination)

    @staticmethod
    def _capture_sync(*, destination: Path) -> None:
        image = ImageGrab.grab(all_screens=False)
        image.save(destination, format="PNG")
