"""Controlled-workstation lifecycle behind the application port."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from mod_debug_pilot.application import BrowserContext, PairingBroker
from mod_debug_pilot.application.ports import WebHost
from mod_debug_pilot.domain import AgentSettings, InstanceSnapshot, PairingRequest
from mod_debug_pilot.infrastructure.agent_runtime import AgentRuntimeConfig, RemoteAgentRuntime
from mod_debug_pilot.infrastructure.profiles import (
    ProfileWorkspace,
    ThunderstoreFetcher,
    ThunderstoreProfileImporter,
)
from mod_debug_pilot.infrastructure.settings import write_json_atomic
from mod_debug_pilot.infrastructure.web_host import (
    controller_http_url,
    discover_controller_hosts,
    preferred_controller_host,
)


class WebHostFactory(Protocol):
    """Build one Web host around a composed browser context."""

    def __call__(
        self,
        *,
        context: BrowserContext,
        allowed_hosts: tuple[str, ...],
    ) -> WebHost:
        """Return one stopped Web host."""
        ...


class LocalAgentHost:
    """Own the one HTTP listener and the shared game runtime."""

    def __init__(self, *, application_data: Path, web_host_factory: WebHostFactory) -> None:
        """Create a stopped host with an explicit browser-host factory."""
        self._application_data = application_data
        self._settings_path = application_data / "agent-settings.json"
        self._web_host_factory = web_host_factory
        self._web: WebHost | None = None
        self._runtime: RemoteAgentRuntime | None = None
        self._pairing: PairingBroker | None = None

    def load_settings(self) -> AgentSettings:
        """Load non-secret settings, falling back after invalid local state."""
        if self._settings_path.is_file():
            try:
                return AgentSettings.from_mapping(
                    value=json.loads(self._settings_path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        root = self._application_data / "agent-runtime"
        save = Path.home() / "AppData" / "LocalLow" / "ZeekerssRBLX" / "Lethal Company"
        return AgentSettings(
            data_root=str(root),
            artifact_root=str(root / "artifacts"),
            save_directory=str(save),
        )

    async def start(self, *, settings: AgentSettings) -> str:
        """Start the Agent-hosted HTTP controller and recover stale state first."""
        if self._web is not None:
            raise OSError("Controller listener is already running.")
        write_json_atomic(path=self._settings_path, payload=settings.to_mapping())
        runtime = RemoteAgentRuntime.system_default(
            config=AgentRuntimeConfig(
                game_executable=Path(settings.game_executable),
                data_root=Path(settings.data_root),
                artifact_root=Path(settings.artifact_root),
                save_directory=Path(settings.save_directory),
            )
        )
        pairing = PairingBroker()
        fetcher = ThunderstoreFetcher()
        context = BrowserContext(
            pairing=pairing,
            importer=ThunderstoreProfileImporter(fetcher=fetcher),
            workspace=ProfileWorkspace(fetcher=fetcher),
            runtime=runtime,
            data_root=Path(settings.data_root),
        )
        hosts = discover_controller_hosts(bind_host=settings.bind_host)
        web = self._web_host_factory(context=context, allowed_hosts=hosts)
        try:
            await runtime.recover()
            await web.start(host=settings.bind_host, port=settings.web_port)
        except BaseException:
            try:
                await web.stop()
            finally:
                await runtime.shutdown()
            raise
        self._runtime = runtime
        self._pairing = pairing
        self._web = web
        preferred_host = preferred_controller_host(hosts=hosts)
        return controller_http_url(host=preferred_host, port=settings.web_port)

    async def stop(self) -> None:
        """Stop the browser listener and restore all workstation state."""
        web = self._web
        runtime = self._runtime
        self._web = None
        self._runtime = None
        self._pairing = None
        try:
            if web is not None:
                await web.stop()
        finally:
            if runtime is not None:
                await runtime.shutdown()

    def open_pairing(self) -> str:
        """Open one local-approval window."""
        return self._require_pairing().open()

    def pending_pairings(self) -> tuple[PairingRequest, ...]:
        """Return browser sessions awaiting a decision."""
        return self._require_pairing().pending()

    def decide_pairing(self, *, request_id: str, approve: bool) -> None:
        """Apply one explicit local decision."""
        self._require_pairing().decide(request_id=request_id, approve=approve)

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Return all tracked instances while the host is running."""
        return await self._require_runtime().list_instances()

    async def stop_instance(self, *, instance_id: str) -> None:
        """Stop exactly one tracked instance."""
        await self._require_runtime().stop(instance_id=instance_id)

    def _require_pairing(self) -> PairingBroker:
        if self._pairing is None:
            raise OSError("Start the controller listener first.")
        return self._pairing

    def _require_runtime(self) -> RemoteAgentRuntime:
        if self._runtime is None:
            raise OSError("Start the controller listener first.")
        return self._runtime
