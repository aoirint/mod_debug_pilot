"""Tests for the minimal operating-system runner adapters."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Coroutine
from datetime import UTC
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from mod_debug_pilot.infrastructure.runner import (
    AsyncioProcessLauncher,
    AsyncioRunningProcess,
    PillowScreenCapturer,
    SystemClock,
)


def test_system_clock() -> None:
    """Production time remains UTC and monotonic."""

    async def scenario() -> None:
        clock = SystemClock()
        assert clock.now().tzinfo is UTC
        first = clock.monotonic()
        await clock.sleep(seconds=0)
        assert clock.monotonic() >= first

    asyncio.run(scenario())


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
