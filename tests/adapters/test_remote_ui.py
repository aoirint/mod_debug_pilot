"""Semantic tests for the native Agent and browser Flet adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import flet as ft
import pytest

from mod_debug_pilot.application.services import DownloadedArtifact
from mod_debug_pilot.domain import (
    AgentSettings,
    InstanceSnapshot,
    InstanceSpec,
    InstanceStatus,
    PairingRequest,
)
from mod_debug_pilot.presentation import (
    AgentController,
    AgentViewState,
    BrowserController,
    BrowserViewState,
)
from mod_debug_pilot.ui.agent_app import AgentView, configure_agent_page
from mod_debug_pilot.ui.web_controller import WebControllerView, configure_web_controller


class FakePage:
    """Small Flet page surface used by both adapters."""

    def __init__(self) -> None:
        self.title = ""
        self.theme: ft.Theme | None = None
        self.padding: int | None = None
        self.on_close: object | None = None
        self.on_disconnect: object | None = None
        self.controls: list[ft.Control] = []
        self.update_count = 0

    def add(  # keyword-only-exception: Flet page ABI accepts variadic controls.
        self, *controls: ft.Control
    ) -> None:
        self.controls.extend(controls)

    def update(self) -> None:
        self.update_count += 1


def instance(*, status: InstanceStatus = InstanceStatus.RUNNING) -> InstanceSnapshot:
    """Return one visible instance row."""
    return InstanceSnapshot(
        instance_id="instance",
        name="client-1",
        profile_id="profile",
        status=status,
        pid=42,
        started_at="now",
    )


class BrowserControllerStub:
    """Drive browser view state without external effects."""

    def __init__(self) -> None:
        self.state = BrowserViewState()
        self.calls: list[str] = []
        self.closed = 0

    def request_pairing(self, *, code: str, controller_name: str) -> None:
        assert (code, controller_name) == ("12345678", "Browser")
        self.calls.append("request")
        self.state = replace(self.state, status="requested")

    def check_pairing(self) -> None:
        self.calls.append("check")
        self.state = replace(self.state, approved=True, status="approved")

    async def prepare_profile(
        self,
        *,
        profile_id: str,
        profile_code: str,
        local_mod_name: str,
        local_mod_bytes: bytes,
    ) -> None:
        assert (profile_id, profile_code, local_mod_name, local_mod_bytes) == (
            "profile",
            "profile-code",
            "Local.dll",
            b"dll",
        )
        self.calls.append("prepare")
        self.state = replace(
            self.state,
            config_files=("nested/plugin.cfg",),
            status="prepared",
        )

    def load_config(self, *, relative: str) -> None:
        assert relative == "nested/plugin.cfg"
        self.calls.append("load")
        self.state = replace(self.state, config_content="Enabled = true")

    def save_config(self, *, relative: str, content: str) -> None:
        assert (relative, content) == ("nested/plugin.cfg", "Enabled = false")
        self.calls.append("save")

    async def install_profile(self, *, profile_id: str) -> None:
        assert profile_id == "profile"
        self.calls.append("install")
        self.state = replace(self.state, config_files=(), config_content="")

    async def launch(
        self,
        *,
        name: str,
        profile_id: str,
        debugger_port: str,
    ) -> InstanceSpec:
        self.calls.append("launch")
        self.state = replace(self.state, instances=(instance(),))
        return InstanceSpec(
            name=name,
            profile_id=profile_id,
            debugger_port=int(debugger_port),
        )

    async def refresh_instances(self) -> None:
        self.calls.append("refresh")
        self.state = replace(
            self.state,
            instances=(instance(), instance(status=InstanceStatus.STOPPED)),
        )

    async def stop_instance(self, *, instance_id: str) -> None:
        assert instance_id == "instance"
        self.calls.append("stop")

    async def capture(self, *, instance_id: str) -> DownloadedArtifact:
        assert instance_id == "instance"
        self.calls.append("capture")
        return DownloadedArtifact(name="screen.png", content=b"png")

    async def close(self) -> None:
        self.closed += 1

    def fail(self, *, message: str) -> None:
        self.state = replace(self.state, status=message)


class AgentControllerStub:
    """Drive native view state without listener or process effects."""

    def __init__(self) -> None:
        settings = AgentSettings(
            agent_name="Agent",
            bind_host="127.0.0.1",
            game_executable="game.exe",
            data_root="data",
            artifact_root="artifacts",
            save_directory="saves",
        )
        self.state = AgentViewState(settings=settings)
        self.calls: list[str] = []
        self.closed = 0

    async def start(self, *, values: dict[str, object]) -> None:
        assert values["agent_name"] == "Agent"
        self.calls.append("start")
        self.state = replace(
            self.state,
            running=True,
            status="running",
            controller_url="Controller URL: http://agent/",
        )

    async def stop(self) -> None:
        self.calls.append("stop")
        self.state = replace(self.state, running=False, status="stopped")

    def open_pairing(self) -> None:
        self.calls.append("open")
        self.state = replace(self.state, pairing_code="Pairing code: 12345678")

    def refresh_pairings(self) -> None:
        self.calls.append("pairings")
        self.state = replace(
            self.state,
            pending=(
                PairingRequest(
                    request_id="request",
                    controller_id="controller",
                    controller_name="Browser",
                    poll_token="poll",
                    created_at=1,
                ),
            ),
        )

    def decide_pairing(self, *, request_id: str, controller_name: str, approve: bool) -> None:
        assert (request_id, controller_name, approve) == ("request", "Browser", True)
        self.calls.append("decide")
        self.state = replace(self.state, pending=(), status="Approved Browser.")

    async def refresh_instances(self) -> None:
        self.calls.append("instances")
        self.state = replace(self.state, instances=(instance(),))

    async def stop_instance(self, *, instance_id: str) -> None:
        assert instance_id == "instance"
        self.calls.append("kill")

    async def close(self) -> None:
        self.closed += 1

    def fail(self, *, message: str) -> None:
        self.state = replace(self.state, status=message)


def make_web_view() -> tuple[WebControllerView, FakePage, BrowserControllerStub]:
    """Create one unattached browser view."""
    page = FakePage()
    controller = BrowserControllerStub()
    view = WebControllerView(
        page=cast(ft.Page, page),
        controller=cast(BrowserController, controller),
    )
    return view, page, controller


def test_web_controller_complete_ui_flow() -> None:
    """The browser adapter emits every supported action and renders state."""

    async def scenario() -> None:
        view, page, controller = make_web_view()
        assert all(control.disabled for control in view._privileged_controls)  # noqa: SLF001
        view.controller_name.value = "Browser"
        view.pairing_code.value = "12345678"
        await view._request_pairing_action()  # noqa: SLF001
        await view._check_approval_action()  # noqa: SLF001
        view._render()  # noqa: SLF001
        assert not all(control.disabled for control in view._privileged_controls)  # noqa: SLF001

        cast(Any, view._picker).pick_files = AsyncMock(  # noqa: SLF001
            return_value=[SimpleNamespace(name="Local.dll", bytes=b"dll")]
        )
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        view.profile_id.value = "profile"
        view.profile_code.value = "profile-code"
        await view._import_profile_action()  # noqa: SLF001
        view._render()  # noqa: SLF001
        view.config_selector.value = "nested/plugin.cfg"
        await view._load_config_action()  # noqa: SLF001
        view._render()  # noqa: SLF001
        view.config_editor.value = "Enabled = false"
        await view._save_config_action()  # noqa: SLF001
        await view._install_profile_action()  # noqa: SLF001
        view.instance_name.value = "client-1"
        view.debugger_port.value = "55555"
        await view._launch_action()  # noqa: SLF001
        assert view.instance_name.value == "client-2"
        assert view.debugger_port.value == "55556"
        await view._refresh(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        view._render()  # noqa: SLF001
        assert len(view.instances.controls) == 2
        cast(Any, view._picker).save_file = AsyncMock(return_value=None)  # noqa: SLF001
        await view._capture_handler(instance_id="instance")()  # noqa: SLF001
        await view._stop_handler(instance_id="instance")()  # noqa: SLF001
        assert {"request", "check", "prepare", "load", "save", "install", "launch"} <= set(
            controller.calls
        )
        assert page.update_count > 0
        await view.close()
        assert controller.closed == 1

    asyncio.run(scenario())


def test_web_controller_validation_and_event_wrappers() -> None:
    """Canceled uploads, invalid files, missing inputs, and wrapper events stay bounded."""

    async def scenario() -> None:
        view, _page, controller = make_web_view()
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        controller.state = replace(controller.state, approved=True)
        cast(Any, view._picker).pick_files = AsyncMock(return_value=None)  # noqa: SLF001
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        cast(Any, view._picker).pick_files = AsyncMock(  # noqa: SLF001
            return_value=[SimpleNamespace(name="bad.dll", bytes=None)]
        )
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        assert "could not" in controller.state.status
        with pytest.raises(ValueError, match="Select"):
            await view._import_profile_action()  # noqa: SLF001
        with pytest.raises(ValueError, match="configuration"):
            view._selected_config()  # noqa: SLF001
        view._increment_instance_fields(  # noqa: SLF001
            spec=InstanceSpec(name="client", profile_id="profile", debugger_port=6000)
        )
        assert view.instance_name.value != "client"

        wrappers = (
            (view._request_pairing, "_request_pairing_action"),  # noqa: SLF001
            (view._check_approval, "_check_approval_action"),  # noqa: SLF001
            (view._import_profile, "_import_profile_action"),  # noqa: SLF001
            (view._load_config, "_load_config_action"),  # noqa: SLF001
            (view._save_config, "_save_config_action"),  # noqa: SLF001
            (view._install_profile, "_install_profile_action"),  # noqa: SLF001
            (view._launch, "_launch_action"),  # noqa: SLF001
        )
        for method, action_name in wrappers:
            with patch.object(view, action_name, AsyncMock()) as action:
                await method(ft.Event("click", ft.Button()))  # type: ignore[arg-type]
                action.assert_awaited_once()

        async def fail() -> None:
            raise OSError("visible")

        await view._perform(action=fail)  # noqa: SLF001
        assert controller.state.status == "visible"

    asyncio.run(scenario())


def test_agent_view_complete_ui_flow() -> None:
    """The native adapter emits listener, approval, instance, and close actions."""

    async def scenario() -> None:
        page = FakePage()
        controller = AgentControllerStub()
        view = AgentView(
            page=cast(ft.Page, page),
            controller=cast(AgentController, controller),
        )
        cast(Any, view._picker).pick_files = AsyncMock(  # noqa: SLF001
            return_value=[SimpleNamespace(path=r"C:\Games\Lethal Company.exe")]
        )
        await view._choose_game_executable(  # noqa: SLF001
            ft.Event("click", ft.OutlinedButton())
        )
        assert view.fields["game_executable"].value == r"C:\Games\Lethal Company.exe"
        cast(Any, view._picker).get_directory_path = AsyncMock(  # noqa: SLF001
            side_effect=[r"C:\MDP\data", r"C:\MDP\artifacts", r"C:\LC\saves", None]
        )
        for field_name in ("data_root", "artifact_root", "save_directory"):
            callback = cast(
                Callable[[ft.Event[ft.OutlinedButton]], Awaitable[None]],
                view.path_buttons[field_name].on_click,
            )
            await callback(ft.Event("click", view.path_buttons[field_name]))
        assert view.fields["data_root"].value == r"C:\MDP\data"
        assert view.fields["artifact_root"].value == r"C:\MDP\artifacts"
        assert view.fields["save_directory"].value == r"C:\LC\saves"
        canceled = cast(
            Callable[[ft.Event[ft.OutlinedButton]], Awaitable[None]],
            view.path_buttons["data_root"].on_click,
        )
        await canceled(ft.Event("click", view.path_buttons["data_root"]))
        await view._start(ft.Event("click", ft.Button()))  # noqa: SLF001
        view._render()  # noqa: SLF001
        assert view.start_button.disabled
        assert all(button.disabled for button in view.path_buttons.values())
        await view._open_pairing(ft.Event("click", ft.Button()))  # noqa: SLF001
        await view._refresh_pending(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        view._render()  # noqa: SLF001
        request = controller.state.pending[0]
        await view._decision_handler(request=request, approve=True)()  # noqa: SLF001
        await view._refresh_instances(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        view._render()  # noqa: SLF001
        assert view.instances.controls
        await view._kill_handler(instance_id="instance")(  # noqa: SLF001
            ft.Event("click", ft.Button())
        )
        await view._stop(ft.Event("click", ft.Button()))  # noqa: SLF001

        async def fail() -> None:
            raise OSError("visible")

        await view._perform(action=fail)  # noqa: SLF001
        assert controller.state.status == "visible"
        await view.close()
        assert controller.closed == 1
        assert page.update_count > 0

    asyncio.run(scenario())


def test_agent_file_picker_cancellation_preserves_the_path() -> None:
    """Canceling or receiving a pathless file selection keeps typed input."""

    async def scenario() -> None:
        page = FakePage()
        controller = AgentControllerStub()
        view = AgentView(
            page=cast(ft.Page, page),
            controller=cast(AgentController, controller),
        )
        original = view.fields["game_executable"].value
        cast(Any, view._picker).pick_files = AsyncMock(  # noqa: SLF001
            side_effect=[None, [SimpleNamespace(path=None)]]
        )
        event = ft.Event("click", ft.OutlinedButton())
        await view._choose_game_executable(event)  # noqa: SLF001
        await view._choose_game_executable(event)  # noqa: SLF001
        assert view.fields["game_executable"].value == original
        assert page.update_count == 0

    asyncio.run(scenario())


def test_page_configuration_and_close_handlers() -> None:
    """Both Flet page entries mount views and own close/disconnect cleanup."""

    async def scenario() -> None:
        agent_page = FakePage()
        agent = AgentControllerStub()
        await configure_agent_page(
            cast(ft.Page, agent_page),
            controller=cast(AgentController, agent),
        )
        assert agent_page.title == "ModDebugPilot Agent"
        assert agent_page.controls
        browser_page = FakePage()
        browser = BrowserControllerStub()
        await configure_web_controller(
            cast(ft.Page, browser_page),
            controller=cast(BrowserController, browser),
        )
        assert browser_page.title == "ModDebugPilot Controller"
        assert browser_page.controls
        for page in (agent_page, browser_page):
            for callback in (page.on_close, page.on_disconnect):
                resolved = cast(Callable[[ft.Event[ft.Page]], Any], callback)
                await cast(asyncio.Task[None], resolved(ft.Event("close", cast(ft.Page, page))))
        assert agent.closed == 2
        assert browser.closed == 2

    asyncio.run(scenario())
