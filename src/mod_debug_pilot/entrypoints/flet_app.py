"""Thin Flet page-session entry point."""

from __future__ import annotations

import flet as ft

from mod_debug_pilot.composition import compose_controller
from mod_debug_pilot.ui import PilotView, configure_page


async def app_main(page: ft.Page) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet invokes page entrypoints positionally.
    """Compose and mount one desktop page session."""
    configure_page(page)
    view = PilotView(page=page, controller=compose_controller())
    page.add(view.build())
    await view.mount()
