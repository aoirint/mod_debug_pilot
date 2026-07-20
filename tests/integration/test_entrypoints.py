"""Tests for import-safe launch entry points."""

from __future__ import annotations

import asyncio
import runpy
import sys
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import flet as ft

from mod_debug_pilot.__main__ import main
from mod_debug_pilot.entrypoints.agent_flet_app import agent_app_main
from mod_debug_pilot.presentation import AgentController
from tests.adapters.test_remote_ui import FakePage


def test_main_launches_flet_with_the_page_entry() -> None:
    """The GUI script delegates exactly once to Flet."""
    with patch("mod_debug_pilot.__main__.ft.run") as launch:
        main()

    launch.assert_called_once_with(agent_app_main)


def test_module_execution_uses_the_same_main() -> None:
    """`python -m mod_debug_pilot` follows the tested launch boundary."""
    imported = sys.modules.pop("mod_debug_pilot.__main__")
    try:
        with patch("flet.run") as launch:
            runpy.run_module("mod_debug_pilot.__main__", run_name="__main__")
    finally:
        sys.modules["mod_debug_pilot.__main__"] = imported

    launch.assert_called_once()


def test_agent_page_entry_composes_and_configures() -> None:
    """The thin session entry composes before page configuration."""
    configure = AsyncMock()
    controller = cast(AgentController, Mock())
    compose = Mock(return_value=controller)
    with (
        patch(
            "mod_debug_pilot.entrypoints.agent_flet_app.compose_agent_controller",
            compose,
        ),
        patch(
            "mod_debug_pilot.entrypoints.agent_flet_app.configure_agent_page",
            configure,
        ),
    ):
        asyncio.run(agent_app_main(cast(ft.Page, FakePage())))
    compose.assert_called_once_with()
    configure.assert_awaited_once()
    call = configure.await_args
    assert call is not None
    assert call.kwargs["controller"] is controller
