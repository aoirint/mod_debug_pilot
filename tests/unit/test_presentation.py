"""Tests for Flet-free presentation controllers."""

from __future__ import annotations

import asyncio
from typing import cast

from mod_debug_pilot.application.services import (
    BrowserSession,
    DownloadedArtifact,
    InstalledProfile,
    ProfileDraft,
)
from mod_debug_pilot.domain import (
    AgentSettings,
    InstanceSnapshot,
    InstanceSpec,
    InstanceStatus,
    PairingRequest,
)
from mod_debug_pilot.presentation import AgentController, BrowserController


def snapshot() -> InstanceSnapshot:
    """Return one tracked instance."""
    return InstanceSnapshot(
        instance_id="instance",
        name="client-1",
        profile_id="profile",
        status=InstanceStatus.RUNNING,
        pid=12,
        started_at="now",
    )


class HostStub:
    """Record native Agent controller effects."""

    def __init__(self) -> None:
        self.settings = AgentSettings(
            agent_name="Agent",
            bind_host="127.0.0.1",
            game_executable="game.exe",
            data_root="data",
            artifact_root="artifacts",
            save_directory="saves",
        )
        self.request = PairingRequest(
            request_id="request",
            controller_id="controller",
            controller_name="Browser",
            poll_token="poll",
            created_at=1,
        )
        self.stopped: list[str] = []
        self.closed = 0

    def load_settings(self) -> AgentSettings:
        return self.settings

    async def start(self, *, settings: AgentSettings) -> str:
        self.settings = settings
        return "http://127.0.0.1:48951/controller"

    async def stop(self) -> None:
        self.closed += 1

    def open_pairing(self) -> str:
        return "12345678"

    def pending_pairings(self) -> tuple[PairingRequest, ...]:
        return (self.request,)

    def decide_pairing(self, *, request_id: str, approve: bool) -> None:
        assert request_id == "request"
        assert approve

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        return (snapshot(),)

    async def stop_instance(self, *, instance_id: str) -> None:
        self.stopped.append(instance_id)


class SessionStub:
    """Return deterministic browser-session results."""

    def __init__(self) -> None:
        self.decisions: list[bool | None] = [None, True]
        self.saved: tuple[str, str] | None = None
        self.closed = False

    def request_pairing(self, *, code: str, controller_name: str) -> None:
        assert (code, controller_name) == ("12345678", "Browser")

    def check_pairing(self) -> bool | None:
        return self.decisions.pop(0)

    async def prepare_profile(
        self,
        *,
        profile_id: str,
        profile_code: str,
        local_mod_name: str,
        local_mod_bytes: bytes,
    ) -> ProfileDraft:
        assert (profile_id, profile_code, local_mod_name, local_mod_bytes) == (
            "profile",
            "code",
            "Local.dll",
            b"dll",
        )
        return ProfileDraft(declared_mods=2, config_files=("a.cfg",))

    def read_config(self, *, relative: str) -> str:
        assert relative == "a.cfg"
        return "Enabled = true"

    def write_config(self, *, relative: str, content: str) -> None:
        self.saved = (relative, content)

    async def install_profile(self, *, profile_id: str) -> InstalledProfile:
        assert profile_id == "profile"
        return InstalledProfile(name="profile", file_count=3)

    async def launch(
        self,
        *,
        name: str,
        profile_id: str,
        debugger_port: int,
    ) -> InstanceSpec:
        return InstanceSpec(name=name, profile_id=profile_id, debugger_port=debugger_port)

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        return (snapshot(),)

    async def stop(self, *, instance_id: str) -> None:
        assert instance_id == "instance"

    async def capture(self, *, instance_id: str) -> DownloadedArtifact:
        assert instance_id == "instance"
        return DownloadedArtifact(name="screen.png", content=b"png")

    async def close(self) -> None:
        self.closed = True


def test_agent_controller_transitions() -> None:
    """The native controller owns listener, approval, and recovery state."""
    host = HostStub()
    controller = AgentController(host=host)
    values = host.settings.to_mapping()

    async def scenario() -> None:
        await controller.start(values=values)
        assert controller.state.running
        assert "http://" in controller.state.controller_url
        controller.open_pairing()
        assert "12345678" in controller.state.pairing_code
        controller.refresh_pairings()
        assert controller.state.pending == (host.request,)
        controller.decide_pairing(
            request_id="request",
            controller_name="Browser",
            approve=True,
        )
        assert controller.state.status == "Approved Browser."
        await controller.refresh_instances()
        assert controller.state.instances == (snapshot(),)
        await controller.stop_instance(instance_id="instance")
        assert host.stopped == ["instance"]
        controller.fail(message="failure")
        assert controller.state.status == "failure"
        await controller.stop()
        await controller.close()
        assert not controller.state.running

    asyncio.run(scenario())
    assert host.closed == 2


def test_browser_controller_transitions() -> None:
    """The browser controller owns pairing, profile, and instance view state."""
    session = SessionStub()
    controller = BrowserController(session=cast(BrowserSession, session))

    async def scenario() -> None:
        controller.request_pairing(code="12345678", controller_name="Browser")
        controller.check_pairing()
        assert "waiting" in controller.state.status
        controller.check_pairing()
        assert controller.state.approved
        await controller.prepare_profile(
            profile_id="profile",
            profile_code="code",
            local_mod_name="Local.dll",
            local_mod_bytes=b"dll",
        )
        assert controller.state.config_files == ("a.cfg",)
        controller.load_config(relative="a.cfg")
        assert controller.state.config_content == "Enabled = true"
        controller.save_config(relative="a.cfg", content="Enabled = false")
        assert session.saved == ("a.cfg", "Enabled = false")
        await controller.install_profile(profile_id="profile")
        spec = await controller.launch(
            name="client-1",
            profile_id="profile",
            debugger_port="55555",
        )
        assert spec.debugger_port == 55555
        assert controller.state.instances == (snapshot(),)
        await controller.stop_instance(instance_id="instance")
        artifact = await controller.capture(instance_id="instance")
        assert artifact.content == b"png"
        controller.fail(message="failure")
        assert controller.state.status == "failure"
        await controller.close()

    asyncio.run(scenario())
    assert session.closed


def test_browser_controller_rejected_pairing() -> None:
    """A rejected request never becomes approved."""
    session = SessionStub()
    session.decisions = [False]
    controller = BrowserController(session=cast(BrowserSession, session))
    controller.check_pairing()
    assert not controller.state.approved
    assert "rejected" in controller.state.status
