"""Thin native Agent page-session entry point."""

from __future__ import annotations

import os
from pathlib import Path

import flet as ft

from mod_debug_pilot.infrastructure import application_data_dir
from mod_debug_pilot.ui.agent_app import configure_agent_page


async def agent_app_main(page: ft.Page) -> None:
    """Mount one controlled-side Agent session."""
    data = application_data_dir(os.environ, home=Path.home()) / "agent"
    await configure_agent_page(page, application_data=data)
