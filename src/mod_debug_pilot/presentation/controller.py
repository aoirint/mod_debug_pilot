"""Flet-free controllers that own visible state transitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from mod_debug_pilot.application.ports import AgentHost
from mod_debug_pilot.application.services import BrowserSession, DownloadedArtifact
from mod_debug_pilot.domain import AgentSettings, InstanceSpec
from mod_debug_pilot.presentation.models import AgentViewState, BrowserViewState


class AgentController:
    """Own native Agent state and delegate effects to one host port."""

    def __init__(self, *, host: AgentHost) -> None:
        """Initialize from the host's persisted non-secret settings."""
        self._host = host
        self.state = AgentViewState(settings=host.load_settings())

    async def start(self, *, values: Mapping[str, object]) -> None:
        """Validate settings and start the sole browser control plane."""
        settings = AgentSettings.from_mapping(value=dict(values))
        url = await self._host.start(settings=settings)
        self.state = AgentViewState(
            settings=settings,
            running=True,
            status="Trusted-LAN HTTP controller is running.",
            controller_url=f"Controller URL: {url}",
        )

    async def stop(self) -> None:
        """Stop the host and restore workstation state."""
        await self._host.stop()
        self.state = AgentViewState(
            settings=self.state.settings,
            status="Controller stopped; game bootstrap and normal saves restored.",
        )

    def open_pairing(self) -> None:
        """Open and expose one short-lived pairing code."""
        code = self._host.open_pairing()
        self.state = replace(
            self.state,
            pairing_code=f"Pairing code: {code} (valid for 10 minutes, one use)",
        )

    def refresh_pairings(self) -> None:
        """Refresh pending browser sessions."""
        self.state = replace(self.state, pending=self._host.pending_pairings())

    def decide_pairing(self, *, request_id: str, controller_name: str, approve: bool) -> None:
        """Apply and display one local operator decision."""
        self._host.decide_pairing(request_id=request_id, approve=approve)
        self.state = replace(
            self.state,
            pending=self._host.pending_pairings(),
            status=f"{'Approved' if approve else 'Rejected'} {controller_name}.",
        )

    async def refresh_instances(self) -> None:
        """Refresh the native recovery view of tracked instances."""
        self.state = replace(self.state, instances=await self._host.list_instances())

    async def stop_instance(self, *, instance_id: str) -> None:
        """Stop one instance and refresh state."""
        await self._host.stop_instance(instance_id=instance_id)
        await self.refresh_instances()

    async def close(self) -> None:
        """Idempotently close the owned host."""
        await self._host.stop()

    def fail(self, *, message: str) -> None:
        """Expose one bounded operation error."""
        self.state = replace(self.state, status=message)


class BrowserController:
    """Own one browser session's view state and workflows."""

    def __init__(self, *, session: BrowserSession) -> None:
        """Create an unauthorized Controller view."""
        self._session = session
        self.state = BrowserViewState()

    def request_pairing(self, *, code: str, controller_name: str) -> None:
        """Submit a connection request for local approval."""
        self._session.request_pairing(code=code, controller_name=controller_name)
        self.state = replace(
            self.state,
            status="Connection requested. Approve it in the Agent native GUI.",
        )

    def check_pairing(self) -> None:
        """Refresh the local operator's decision."""
        decision = self._session.check_pairing()
        if decision is None:
            status = "Still waiting for Agent approval."
        elif decision:
            status = "Approved. This browser session can now control the Agent."
        else:
            status = "The Agent operator rejected this session."
        self.state = replace(self.state, approved=bool(decision), status=status)

    async def prepare_profile(
        self,
        *,
        profile_id: str,
        profile_code: str,
        local_mod_name: str,
        local_mod_bytes: bytes,
    ) -> None:
        """Prepare one profile draft from user-selected inputs."""
        draft = await self._session.prepare_profile(
            profile_id=profile_id,
            profile_code=profile_code,
            local_mod_name=local_mod_name,
            local_mod_bytes=local_mod_bytes,
        )
        self.state = replace(
            self.state,
            config_files=draft.config_files,
            config_content="",
            status=(
                f"Draft prepared with {draft.declared_mods} declared mods and "
                f"{len(draft.config_files)} configs."
            ),
        )

    def load_config(self, *, relative: str) -> None:
        """Load one configuration into presentation state."""
        content = self._session.read_config(relative=relative)
        self.state = replace(
            self.state,
            config_content=content,
            status=f"Loaded {relative}.",
        )

    def save_config(self, *, relative: str, content: str) -> None:
        """Save one configuration from presentation state."""
        self._session.write_config(relative=relative, content=content)
        self.state = replace(
            self.state,
            config_content=content,
            status=f"Saved {relative}.",
        )

    async def install_profile(self, *, profile_id: str) -> None:
        """Install the current verified profile draft."""
        installed = await self._session.install_profile(profile_id=profile_id)
        self.state = replace(
            self.state,
            config_files=(),
            config_content="",
            status=f"Installed {installed.name}: {installed.file_count} verified files.",
        )

    async def launch(
        self,
        *,
        name: str,
        profile_id: str,
        debugger_port: str,
    ) -> InstanceSpec:
        """Launch one tracked instance and refresh the list."""
        spec = await self._session.launch(
            name=name,
            profile_id=profile_id,
            debugger_port=int(debugger_port),
        )
        await self.refresh_instances()
        return spec

    async def refresh_instances(self) -> None:
        """Refresh all tracked instance rows."""
        instances = await self._session.list_instances()
        self.state = replace(
            self.state,
            instances=instances,
            status=f"Loaded {len(instances)} instance records.",
        )

    async def stop_instance(self, *, instance_id: str) -> None:
        """Stop one tracked instance and refresh the list."""
        await self._session.stop(instance_id=instance_id)
        await self.refresh_instances()

    async def capture(self, *, instance_id: str) -> DownloadedArtifact:
        """Capture one screenshot for browser download."""
        artifact = await self._session.capture(instance_id=instance_id)
        self.state = replace(self.state, status=f"Downloaded screenshot {artifact.name}.")
        return artifact

    async def close(self) -> None:
        """Discard this page session's unfinished profile draft."""
        await self._session.close()

    def fail(self, *, message: str) -> None:
        """Expose one bounded operation error."""
        self.state = replace(self.state, status=message)
