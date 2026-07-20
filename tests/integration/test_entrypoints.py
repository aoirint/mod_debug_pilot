"""Tests for import-safe launch entry points."""

from __future__ import annotations

import runpy
import sys
from unittest.mock import patch

from mod_debug_pilot.__main__ import main
from mod_debug_pilot.entrypoints.flet_app import app_main


def test_main_launches_flet_with_the_page_entry() -> None:
    """The GUI script delegates exactly once to Flet."""
    with patch("mod_debug_pilot.__main__.ft.run") as launch:
        main()

    launch.assert_called_once_with(app_main)


def test_module_execution_uses_the_same_main() -> None:
    """`python -m mod_debug_pilot` follows the tested launch boundary."""
    imported = sys.modules.pop("mod_debug_pilot.__main__")
    try:
        with patch("flet.run") as launch:
            runpy.run_module("mod_debug_pilot.__main__", run_name="__main__")
    finally:
        sys.modules["mod_debug_pilot.__main__"] = imported

    launch.assert_called_once()
