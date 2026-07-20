"""Tests for concrete composition and Flet page setup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import patch

import flet as ft

from mod_debug_pilot.composition import compose_controller
from mod_debug_pilot.entrypoints.flet_app import app_main
from mod_debug_pilot.presentation import AppPhase
from tests.adapters.test_ui import FakePage
from tests.unit.test_presentation import make_controller


def test_composition_uses_explicit_and_platform_data_directories(*, tmp_path: Path) -> None:
    """Composition accepts a test seam and otherwise resolves platform storage."""
    explicit = compose_controller(data_dir=tmp_path / "explicit")
    asyncio.run(explicit.initialize())
    assert explicit.state.phase is AppPhase.READY

    with patch(
        "mod_debug_pilot.composition.application.application_data_dir",
        return_value=tmp_path / "platform",
    ) as resolver:
        default = compose_controller()
    asyncio.run(default.initialize())
    resolver.assert_called_once()
    assert default.state.phase is AppPhase.READY


def test_flet_entrypoint_configures_adds_and_mounts_page() -> None:
    """The Flet entry remains a thin composition and mount boundary."""
    page = FakePage()
    controller = make_controller()

    with patch(
        "mod_debug_pilot.entrypoints.flet_app.compose_controller",
        return_value=controller,
    ):
        asyncio.run(app_main(cast(ft.Page, page)))

    assert page.title == "ModDebugPilot"
    assert len(page.controls) == 1
    assert controller.state.phase is AppPhase.READY
