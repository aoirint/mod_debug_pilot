"""Thin native Agent page-session entry point."""

from __future__ import annotations

import flet as ft

from mod_debug_pilot.composition import compose_agent_controller
from mod_debug_pilot.ui.agent_app import configure_agent_page


async def agent_app_main(page: ft.Page) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet invokes page entrypoints positionally.
    """Mount one controlled-side Agent session."""
    await configure_agent_page(page, controller=compose_agent_controller())
