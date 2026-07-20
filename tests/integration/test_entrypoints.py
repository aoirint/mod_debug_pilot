"""Tests for import-safe launch entry points."""

from __future__ import annotations

import asyncio
import runpy
import sys
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import flet as ft

from mod_debug_pilot.__main__ import main
from mod_debug_pilot.entrypoints.agent_flet_app import agent_app_main
from tests.adapters.test_ui import FakePage


def test_main_launches_flet_with_the_page_entry() -> None:
    """The GUI script delegates exactly once to Flet."""
    with patch("mod_debug_pilot.agent_main.ft.run") as launch:
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


def test_agent_module_execution_uses_native_main() -> None:
    """The dedicated GUI module follows the same import-safe boundary."""
    imported = sys.modules.pop("mod_debug_pilot.agent_main", None)
    try:
        with patch("flet.run") as launch:
            runpy.run_module("mod_debug_pilot.agent_main", run_name="__main__")
    finally:
        if imported is not None:
            sys.modules["mod_debug_pilot.agent_main"] = imported
    launch.assert_called_once()


def test_agent_page_entry_resolves_application_data(*, tmp_path: Path) -> None:
    """The thin session entry appends the Agent-owned directory exactly once."""
    configure = AsyncMock()
    with (
        patch(
            "mod_debug_pilot.entrypoints.agent_flet_app.application_data_dir",
            return_value=tmp_path,
        ),
        patch(
            "mod_debug_pilot.entrypoints.agent_flet_app.configure_agent_page",
            configure,
        ),
    ):
        asyncio.run(agent_app_main(cast(ft.Page, FakePage())))
    assert configure.await_args is not None
    assert configure.await_args.kwargs["application_data"] == tmp_path / "agent"
