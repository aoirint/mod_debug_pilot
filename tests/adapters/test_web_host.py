"""Flet ASGI export and HTTPS Web host lifecycle tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import flet as ft
import pytest

from mod_debug_pilot.infrastructure.web_host import FletWebHost, WebHostError
from mod_debug_pilot.ui.web_controller import WebControllerContext
from tests.adapters.test_ui import FakePage

_PASSPHRASE = "secret"  # noqa: S105 - test fixture


class ServerStub:
    """Configurable Uvicorn server lifecycle."""

    def __init__(self, _config: object, *, starts: bool = True, waits: bool = False) -> None:
        """Configure start visibility and optional shutdown wait."""
        self.starts = starts
        self.waits = waits
        self.started = False
        self._should_exit = False
        self._exit = asyncio.Event()

    @property
    def should_exit(self) -> bool:
        """Return whether shutdown was requested."""
        return self._should_exit

    @should_exit.setter
    def should_exit(self, value: bool) -> None:
        """Wake the server when shutdown is requested."""
        self._should_exit = value
        if value:
            self._exit.set()

    async def serve(self) -> None:
        """Expose started state and optionally wait for should-exit."""
        self.started = self.starts
        if self.waits:
            await self._exit.wait()


def context_stub(tmp_path: Path) -> WebControllerContext:
    """Return an opaque context because page composition is patched."""
    return cast(WebControllerContext, Mock(data_root=tmp_path))


def test_web_host_exports_controller_and_starts_stops(tmp_path: Path) -> None:
    """The exported page callback captures context and HTTPS lifecycle is owned."""

    async def run() -> None:
        exported: dict[str, object] = {}

        def fake_run(page_main: object, **kwargs: object) -> object:
            exported["page_main"] = page_main
            exported.update(kwargs)
            return "asgi-app"

        server = ServerStub(Mock())
        with (
            patch("mod_debug_pilot.infrastructure.web_host.ft.run", side_effect=fake_run),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Config") as config,
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Server", return_value=server),
            patch(
                "mod_debug_pilot.infrastructure.web_host.configure_web_controller",
                AsyncMock(),
            ) as configure,
        ):
            context = context_stub(tmp_path)
            host = FletWebHost(
                context=context,
                certificate_path=tmp_path / "cert.pem",
                private_key_path=tmp_path / "key.pem",
                passphrase=_PASSPHRASE,
            )
            callback = cast(Callable[[ft.Page], Awaitable[None]], exported["page_main"])
            await callback(cast(ft.Page, FakePage()))
            configure.assert_awaited_once()
            assert exported["no_cdn"] is True
            await host.start(host="127.0.0.1", port=48951)
            config.assert_called_once()
            with pytest.raises(WebHostError, match="already"):
                await host.start(host="127.0.0.1", port=48951)
            await host.stop()
            assert server.should_exit
            await host.stop()

    asyncio.run(run())


def test_web_host_reports_early_exit_and_timeout(tmp_path: Path) -> None:
    """Both an exited server and one that never becomes ready fail visibly."""

    async def run() -> None:
        with (
            patch("mod_debug_pilot.infrastructure.web_host.ft.run", return_value=Mock()),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Config"),
            patch(
                "mod_debug_pilot.infrastructure.web_host.uvicorn.Server",
                return_value=ServerStub(Mock(), starts=False),
            ),
        ):
            host = FletWebHost(
                context=context_stub(tmp_path),
                certificate_path=tmp_path / "cert",
                private_key_path=tmp_path / "key",
                passphrase=_PASSPHRASE,
            )
            with pytest.raises(WebHostError, match="could not start"):
                await host.start(host="127.0.0.1", port=48951)

        waiting = ServerStub(Mock(), starts=False, waits=True)
        original_sleep = asyncio.sleep

        async def fast_sleep(_seconds: float) -> None:
            await original_sleep(0)

        with (
            patch("mod_debug_pilot.infrastructure.web_host.ft.run", return_value=Mock()),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Config"),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Server", return_value=waiting),
            patch("mod_debug_pilot.infrastructure.web_host.asyncio.sleep", side_effect=fast_sleep),
        ):
            host = FletWebHost(
                context=context_stub(tmp_path),
                certificate_path=tmp_path / "cert",
                private_key_path=tmp_path / "key",
                passphrase=_PASSPHRASE,
            )
            with pytest.raises(WebHostError, match="could not start"):
                await host.start(host="127.0.0.1", port=48951)
            assert waiting.should_exit

    asyncio.run(run())
