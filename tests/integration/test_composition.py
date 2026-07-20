"""Tests for the one native-to-Web composition path."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import flet as ft

from mod_debug_pilot.application import BrowserContext, PairingBroker
from mod_debug_pilot.application.ports import AgentRuntime, ProfileImporter, ProfileWorkspace
from mod_debug_pilot.composition import compose_agent_controller
from mod_debug_pilot.infrastructure.agent_host import LocalAgentHost
from mod_debug_pilot.infrastructure.web_host import FletWebHost
from mod_debug_pilot.presentation import AgentController
from tests.adapters.test_remote_ui import FakePage


def test_composition_wires_agent_and_per_page_browser(*, tmp_path: Path) -> None:
    """Composition is the only place where infrastructure and Flet UI meet."""
    controller = compose_agent_controller(application_data=tmp_path)
    assert isinstance(controller, AgentController)
    host = cast(LocalAgentHost, controller._host)  # noqa: SLF001
    context = BrowserContext(
        pairing=PairingBroker(),
        importer=cast(ProfileImporter, Mock()),
        workspace=cast(ProfileWorkspace, Mock()),
        runtime=cast(AgentRuntime, Mock()),
        data_root=tmp_path,
    )
    web = cast(FletWebHost, Mock())
    with patch(
        "mod_debug_pilot.composition.application.FletWebHost",
        return_value=web,
    ) as web_type:
        built = host._web_host_factory(  # noqa: SLF001
            context=context,
            allowed_hosts=("127.0.0.1",),
        )
    assert built is web
    assert web_type.call_args is not None
    page_main = cast(
        Callable[[ft.Page], Coroutine[Any, Any, None]],
        web_type.call_args.kwargs["page_main"],
    )
    configure = AsyncMock()
    with patch(
        "mod_debug_pilot.composition.application.configure_web_controller",
        configure,
    ):
        asyncio.run(page_main(cast(ft.Page, FakePage())))
    configure.assert_awaited_once()


def test_composition_resolves_default_application_data(*, tmp_path: Path) -> None:
    """Composition owns the platform-specific application-data adapter."""
    with patch(
        "mod_debug_pilot.composition.application.application_data_dir",
        return_value=tmp_path,
    ) as resolve:
        controller = compose_agent_controller()
    resolve.assert_called_once()
    host = cast(LocalAgentHost, controller._host)  # noqa: SLF001
    assert host._application_data == tmp_path / "agent"  # noqa: SLF001


def test_inner_layers_do_not_import_flet_or_infrastructure() -> None:
    """Domain, application, and presentation retain inward-only dependencies."""
    package = Path(__file__).parents[2] / "src" / "mod_debug_pilot"
    forbidden = {
        "domain": ("flet", "mod_debug_pilot.application", "mod_debug_pilot.infrastructure"),
        "application": ("flet", "mod_debug_pilot.infrastructure", "mod_debug_pilot.presentation"),
        "presentation": ("flet", "mod_debug_pilot.infrastructure", "mod_debug_pilot.ui"),
    }
    violations: list[str] = []
    for layer, prefixes in forbidden.items():
        for source in (package / layer).glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imports: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
                elif isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
            violations.extend(
                f"{source.name}: {imported}"
                for imported in imports
                if imported.startswith(prefixes)
            )
    assert not violations, violations
