"""Tests for controlled-workstation lifecycle composition."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from mod_debug_pilot.application import BrowserContext
from mod_debug_pilot.application.ports import WebHost
from mod_debug_pilot.domain import AgentSettings, InstanceSnapshot, InstanceStatus
from mod_debug_pilot.infrastructure.agent_host import LocalAgentHost
from mod_debug_pilot.infrastructure.agent_runtime import RemoteAgentRuntime


class RuntimeStub:
    """Record recovery and shutdown without game processes."""

    def __init__(self) -> None:
        self.recovered = 0
        self.closed = 0
        self.stopped: list[str] = []
        self.snapshot = InstanceSnapshot(
            instance_id="instance",
            name="client",
            profile_id="profile",
            status=InstanceStatus.RUNNING,
            pid=1,
            started_at="now",
        )

    async def recover(self) -> None:
        self.recovered += 1

    async def shutdown(self) -> None:
        self.closed += 1

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        return (self.snapshot,)

    async def stop(self, *, instance_id: str) -> InstanceSnapshot:
        self.stopped.append(instance_id)
        return self.snapshot


class WebStub:
    """Record HTTP listener lifecycle."""

    def __init__(self, *, start_error: OSError | None = None) -> None:
        self.start_error = start_error
        self.started: tuple[str, int] | None = None
        self.closed = 0

    async def start(self, *, host: str, port: int) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = (host, port)

    async def stop(self) -> None:
        self.closed += 1


def settings(*, root: Path) -> AgentSettings:
    """Return one valid host configuration."""
    return AgentSettings(
        agent_name="Agent",
        bind_host="127.0.0.1",
        web_port=48951,
        game_executable=str(root / "Lethal Company.exe"),
        data_root=str(root / "data"),
        artifact_root=str(root / "artifacts"),
        save_directory=str(root / "saves"),
    )


def test_agent_host_start_pair_stop_and_persistence(*, tmp_path: Path) -> None:
    """One Web host owns pairing and runtime until explicit restoration."""
    runtime = RuntimeStub()
    web = WebStub()
    captured: list[BrowserContext] = []

    def web_factory(*, context: BrowserContext, allowed_hosts: tuple[str, ...]) -> WebHost:
        assert "127.0.0.1" in allowed_hosts
        captured.append(context)
        return web

    host = LocalAgentHost(application_data=tmp_path / "app", web_host_factory=web_factory)
    configured = settings(root=tmp_path)

    async def scenario() -> None:
        with patch.object(
            RemoteAgentRuntime,
            "system_default",
            return_value=cast(RemoteAgentRuntime, runtime),
        ):
            url = await host.start(settings=configured)
        assert url.startswith("http://")
        assert url.endswith(":48951/controller")
        assert runtime.recovered == 1
        assert web.started == ("127.0.0.1", 48951)
        with pytest.raises(OSError, match="already"):
            await host.start(settings=configured)
        code = host.open_pairing()
        context = captured[0]
        request = context.pairing.request(code=code, controller_name="Browser")
        assert host.pending_pairings() == (request,)
        host.decide_pairing(request_id=request.request_id, approve=True)
        assert host.pending_pairings() == ()
        assert await host.list_instances() == (runtime.snapshot,)
        await host.stop_instance(instance_id="instance")
        await host.stop()
        await host.stop()

    asyncio.run(scenario())
    assert runtime.stopped == ["instance"]
    assert runtime.closed == 1
    assert web.closed == 1
    persisted = json.loads((tmp_path / "app" / "agent-settings.json").read_text())
    assert persisted["agent_name"] == "Agent"
    assert host.load_settings() == configured
    with pytest.raises(OSError, match="Start"):
        host.open_pairing()
    with pytest.raises(OSError, match="Start"):
        asyncio.run(host.list_instances())


def test_agent_host_defaults_corruption_and_start_rollback(*, tmp_path: Path) -> None:
    """Invalid settings fall back, and listener failure restores runtime state."""
    application_data = tmp_path / "app"
    application_data.mkdir()
    settings_path = application_data / "agent-settings.json"
    settings_path.write_text("{", encoding="utf-8")
    web = WebStub(start_error=OSError("bind failed"))
    runtime = RuntimeStub()

    def web_factory(*, context: BrowserContext, allowed_hosts: tuple[str, ...]) -> WebHost:
        del context, allowed_hosts
        return web

    host = LocalAgentHost(application_data=application_data, web_host_factory=web_factory)
    defaults = host.load_settings()
    assert defaults.data_root.endswith("agent-runtime")
    settings_path.write_text("[]", encoding="utf-8")
    assert host.load_settings().data_root.endswith("agent-runtime")

    async def scenario() -> None:
        with (
            patch.object(
                RemoteAgentRuntime,
                "system_default",
                return_value=cast(RemoteAgentRuntime, runtime),
            ),
            pytest.raises(OSError, match="bind failed"),
        ):
            await host.start(settings=settings(root=tmp_path))

    asyncio.run(scenario())
    assert web.closed == 1
    assert runtime.closed == 1
