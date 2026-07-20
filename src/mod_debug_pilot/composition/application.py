"""Application composition for the native Agent and browser sessions."""

from __future__ import annotations

import os
from pathlib import Path

import flet as ft

from mod_debug_pilot.application import BrowserContext, BrowserSession
from mod_debug_pilot.infrastructure.agent_host import LocalAgentHost
from mod_debug_pilot.infrastructure.settings import application_data_dir
from mod_debug_pilot.infrastructure.web_host import FletWebHost
from mod_debug_pilot.presentation import AgentController, BrowserController
from mod_debug_pilot.ui.web_controller import configure_web_controller


def compose_agent_controller(*, application_data: Path | None = None) -> AgentController:
    """Wire one native Agent controller and its per-page browser factory."""
    resolved_data = application_data
    if resolved_data is None:
        resolved_data = application_data_dir(environment=os.environ, home=Path.home()) / "agent"

    def create_web_host(
        *,
        context: BrowserContext,
        allowed_hosts: tuple[str, ...],
    ) -> FletWebHost:
        async def page_main(  # noqa: PLR0917 -- keyword-only-exception: Flet invokes page entrypoints positionally.
            page: ft.Page,
        ) -> None:
            controller = BrowserController(session=BrowserSession(context=context))
            await configure_web_controller(page, controller=controller)

        return FletWebHost(page_main=page_main, allowed_hosts=allowed_hosts)

    host = LocalAgentHost(
        application_data=resolved_data,
        web_host_factory=create_web_host,
    )
    return AgentController(host=host)
