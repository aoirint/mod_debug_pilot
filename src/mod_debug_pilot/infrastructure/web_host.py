"""Run the Agent-hosted Flet Web controller over the Agent TLS identity."""

from __future__ import annotations

import asyncio
from pathlib import Path

import flet as ft
import uvicorn

from mod_debug_pilot.ui.web_controller import WebControllerContext, configure_web_controller


class WebHostError(OSError):
    """Report failure to start or stop the browser controller listener."""


class FletWebHost:
    """Own one HTTPS Uvicorn server containing the Flet ASGI application."""

    def __init__(
        self,
        *,
        context: WebControllerContext,
        certificate_path: Path,
        private_key_path: Path,
        passphrase: str,
    ) -> None:
        """Create a stopped host around the supplied Agent-owned services."""

        async def page_main(page: ft.Page) -> None:
            await configure_web_controller(page, context=context)

        app = ft.run(
            page_main,
            name="controller",
            view=ft.AppView.WEB_BROWSER,
            export_asgi_app=True,
            no_cdn=True,
        )
        self._app = app
        self._certificate = certificate_path
        self._private_key = private_key_path
        self._passphrase = passphrase
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self, *, host: str, port: int) -> None:
        """Start HTTPS and wait until the socket is accepting connections."""
        if self._task is not None:
            raise WebHostError("Controller Web UI is already running.")
        config = uvicorn.Config(
            self._app,
            host=host,
            port=port,
            ssl_certfile=str(self._certificate),
            ssl_keyfile=str(self._private_key),
            ssl_keyfile_password=self._passphrase,
            access_log=False,
            log_level="warning",
            server_header=False,
            date_header=False,
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(), name="moddebugpilot-controller-web")
        self._server = server
        self._task = task
        for _attempt in range(100):
            if server.started:
                return
            if task.done():
                break
            await asyncio.sleep(0.05)
        await self.stop()
        raise WebHostError("Controller Web UI could not start.")

    async def stop(self) -> None:
        """Request graceful shutdown and await completion."""
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task
        self._server = None
        self._task = None
