"""Native controlled-side Flet GUI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import flet as ft

from mod_debug_pilot.domain import AgentSettings, InstanceSnapshot, InstanceStatus
from mod_debug_pilot.infrastructure.agent_runtime import AgentRuntimeConfig, RemoteAgentRuntime
from mod_debug_pilot.infrastructure.profiles import (
    ProfileWorkspace,
    ThunderstoreFetcher,
    ThunderstoreProfileImporter,
)
from mod_debug_pilot.infrastructure.remote_api import AgentApiIdentity, AgentApiServer
from mod_debug_pilot.infrastructure.security import (
    AgentIdentity,
    AuthorizationStore,
    IdentityError,
    PairingBroker,
    PairingRequest,
    create_agent_identity,
    load_agent_identity,
    server_ssl_context,
)
from mod_debug_pilot.infrastructure.settings import write_json_atomic
from mod_debug_pilot.infrastructure.web_host import (
    FletWebHost,
    controller_http_url,
    discover_controller_hosts,
    preferred_controller_host,
)
from mod_debug_pilot.ui.web_controller import WebControllerContext


class AgentView:
    """Own the explicit listen/approve/stop lifecycle on the test workstation."""

    def __init__(self, page: ft.Page, *, application_data: Path) -> None:
        """Create controls and load no secrets until the operator acts."""
        self._page = page
        self._application_data = application_data
        self._settings_path = application_data / "agent-settings.json"
        self._api: AgentApiServer | None = None
        self._web: FletWebHost | None = None
        self._runtime: RemoteAgentRuntime | None = None
        self._pairing: PairingBroker | None = None
        self._identity: AgentIdentity | None = None
        defaults = self._load_defaults()
        self.fields = {
            "agent_name": ft.TextField(label="Agent name", value=defaults.agent_name),
            "bind_host": ft.TextField(label="Bind host", value=defaults.bind_host),
            "api_port": ft.TextField(label="Automation API port", value=str(defaults.api_port)),
            "web_port": ft.TextField(label="Controller Web port", value=str(defaults.web_port)),
            "game_executable": ft.TextField(
                label="Lethal Company executable", value=defaults.game_executable
            ),
            "data_root": ft.TextField(label="Agent data root", value=defaults.data_root),
            "artifact_root": ft.TextField(label="Artifact root", value=defaults.artifact_root),
            "save_directory": ft.TextField(
                label="Lethal Company save directory", value=defaults.save_directory
            ),
        }
        self.passphrase = ft.TextField(
            label="Automation API TLS passphrase (not saved)",
            password=True,
            can_reveal_password=True,
        )
        self.status = ft.Text("Listener stopped.", selectable=True)
        self.fingerprint = ft.Text(
            "Automation API certificate fingerprint: not loaded", selectable=True
        )
        self.controller_url = ft.Text("Controller URL: listener stopped", selectable=True)
        self.pairing_code = ft.Text("Pairing code: closed", selectable=True, size=20)
        self.pending = ft.Column(spacing=8)
        self.instances = ft.Column(spacing=8)
        self.progress = ft.ProgressRing(width=20, height=20, visible=False)
        self.start_button = ft.Button("Start LAN listeners", on_click=self._start)
        self.stop_button = ft.Button("Stop and restore", on_click=self._stop, disabled=True)
        self.open_pairing_button = ft.Button(
            "Open pairing window", on_click=self._open_pairing, disabled=True
        )
        self._root = self._build()

    def build(self) -> ft.Control:
        """Return the stable root control."""
        return self._root

    async def close(self) -> None:
        """Stop listeners and recover workstation state on window close."""
        await self._stop_services()

    def _build(self) -> ft.Control:
        header = ft.Container(
            bgcolor=ft.Colors.BLUE_GREY_900,
            padding=ft.Padding.symmetric(horizontal=28, vertical=20),
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.COMPUTER, color=ft.Colors.WHITE, size=30),
                    ft.Column(
                        [
                            ft.Text(
                                "ModDebugPilot Agent",
                                color=ft.Colors.WHITE,
                                size=24,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                "Controlled workstation — local approval and recovery",
                                color=ft.Colors.BLUE_GREY_200,
                            ),
                        ],
                        spacing=2,
                    ),
                ]
            ),
        )
        settings_controls = list(self.fields.values())
        status_card = self._card(
            "LAN listeners",
            controls=[
                self.passphrase,
                ft.Text(
                    "The browser UI uses plain HTTP for trusted private LANs. "
                    "Traffic is not confidential; restrict the port with Windows Firewall.",
                    color=ft.Colors.AMBER_800,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Row([self.start_button, self.stop_button, self.progress]),
                self.status,
                self.controller_url,
                self.fingerprint,
            ],
        )
        pairing_card = self._card(
            "Connection approval",
            controls=[
                ft.Row(
                    [
                        self.open_pairing_button,
                        ft.OutlinedButton("Refresh requests", on_click=self._refresh_pending),
                    ]
                ),
                self.pairing_code,
                self.pending,
            ],
        )
        instances_card = self._card(
            "Tracked instances",
            controls=[
                ft.OutlinedButton("Refresh instances", on_click=self._refresh_instances),
                self.instances,
            ],
        )
        content = ft.ResponsiveRow(
            [
                ft.Card(
                    col={"sm": 12, "lg": 7},
                    content=ft.Container(
                        padding=20,
                        content=ft.Column(
                            [
                                ft.Text("Workstation settings", size=19, weight=ft.FontWeight.BOLD),
                                ft.Text(
                                    "Paths and ports are saved; the identity passphrase "
                                    "is never saved.",
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                                *settings_controls,
                            ],
                            spacing=12,
                        ),
                    ),
                ),
                ft.Column(
                    col={"sm": 12, "lg": 5},
                    controls=[status_card, pairing_card, instances_card],
                    spacing=16,
                ),
            ],
            spacing=16,
            run_spacing=16,
        )
        return ft.Column(
            [header, ft.Container(content=content, padding=24)],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    @staticmethod
    def _card(title: str, *, controls: list[ft.Control]) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [ft.Text(title, size=19, weight=ft.FontWeight.BOLD), *controls],
                    spacing=12,
                ),
            )
        )

    async def _start(self, _event: ft.Event[ft.Button]) -> None:
        await self._perform(self._start_services)

    async def _start_services(self) -> None:
        if self._api is not None:
            raise OSError("Listeners are already running.")
        settings = AgentSettings.from_mapping(
            {name: field.value for name, field in self.fields.items()}
        )
        passphrase = self.passphrase.value or ""
        write_json_atomic(self._settings_path, payload=settings.to_mapping())
        identity_dir = self._application_data / "identity"
        if (identity_dir / "agent-cert.pem").exists():
            identity = load_agent_identity(identity_dir, passphrase=passphrase)
        else:
            identity = create_agent_identity(
                identity_dir,
                passphrase=passphrase,
                common_name=settings.agent_name,
            )
        runtime_config = AgentRuntimeConfig(
            game_executable=Path(settings.game_executable),
            data_root=Path(settings.data_root),
            artifact_root=Path(settings.artifact_root),
            save_directory=Path(settings.save_directory),
        )
        runtime = RemoteAgentRuntime.system_default(runtime_config)
        authorizations = AuthorizationStore(self._application_data / "approved-controllers.json")
        pairing = PairingBroker(authorizations)
        fetcher = ThunderstoreFetcher()
        context = WebControllerContext(
            pairing=pairing,
            importer=ThunderstoreProfileImporter(fetcher),
            workspace=ProfileWorkspace(fetcher),
            runtime=runtime,
            data_root=Path(settings.data_root),
        )
        api = AgentApiServer(
            identity=AgentApiIdentity(
                agent_name=settings.agent_name,
                fingerprint=identity.fingerprint,
            ),
            runtime=runtime,
            authorizations=authorizations,
            pairing=pairing,
            max_upload_bytes=runtime_config.max_upload_bytes,
        )
        controller_hosts = discover_controller_hosts(settings.bind_host)
        web = FletWebHost(context=context, allowed_hosts=controller_hosts)
        ssl_context = server_ssl_context(identity, passphrase=passphrase)
        try:
            await api.start(
                host=settings.bind_host, port=settings.api_port, ssl_context=ssl_context
            )
            await web.start(host=settings.bind_host, port=settings.web_port)
        except BaseException:
            await web.stop()
            await api.stop()
            await runtime.shutdown()
            raise
        self._identity = identity
        self._runtime = runtime
        self._pairing = pairing
        self._api = api
        self._web = web
        controller_host = preferred_controller_host(controller_hosts)
        self.status.value = "Trusted-LAN HTTP controller and pinned-TLS automation API are running."
        self.controller_url.value = (
            f"Controller URL: {controller_http_url(controller_host, port=settings.web_port)}"
        )
        self.fingerprint.value = f"Automation API SHA-256: {identity.fingerprint}"
        self.start_button.disabled = True
        self.stop_button.disabled = False
        self.open_pairing_button.disabled = False
        for field in self.fields.values():
            field.disabled = True

    async def _stop(self, _event: ft.Event[ft.Button]) -> None:
        await self._perform(self._stop_services)

    async def _stop_services(self) -> None:
        if self._web is not None:
            await self._web.stop()
        if self._api is not None:
            await self._api.stop()
        if self._runtime is not None:
            await self._runtime.shutdown()
        self._web = None
        self._api = None
        self._runtime = None
        self._pairing = None
        self.status.value = "Listeners stopped; game bootstrap and normal saves restored."
        self.controller_url.value = "Controller URL: listener stopped"
        self.pairing_code.value = "Pairing code: closed"
        self.pending.controls = []
        self.start_button.disabled = False
        self.stop_button.disabled = True
        self.open_pairing_button.disabled = True
        for field in self.fields.values():
            field.disabled = False

    async def _open_pairing(self, _event: ft.Event[ft.Button]) -> None:
        if self._pairing is None:
            return
        code = self._pairing.open()
        self.pairing_code.value = f"Pairing code: {code} (valid for 10 minutes, one use)"
        self._page.update()

    async def _refresh_pending(self, _event: ft.Event[ft.OutlinedButton]) -> None:
        if self._pairing is None:
            return
        self.pending.controls = [self._pending_row(item) for item in self._pairing.pending()]
        self._page.update()

    def _pending_row(self, request: PairingRequest) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=12,
                content=ft.Column(
                    [
                        ft.Text(request.controller_name, weight=ft.FontWeight.BOLD),
                        ft.Text(f"Controller ID: {request.controller_id}", selectable=True),
                        ft.Row(
                            [
                                ft.Button(
                                    "Approve",
                                    on_click=self._decision_handler(request, approve=True),
                                ),
                                ft.OutlinedButton(
                                    "Reject",
                                    on_click=self._decision_handler(request, approve=False),
                                ),
                            ]
                        ),
                    ]
                ),
            )
        )

    def _decision_handler(
        self, request: PairingRequest, *, approve: bool
    ) -> Callable[[], Awaitable[None]]:
        async def handler() -> None:
            if self._pairing is not None:
                self._pairing.decide(request.request_id, approve=approve)
                self.pending.controls = [
                    self._pending_row(item) for item in self._pairing.pending()
                ]
                self.status.value = (
                    f"{'Approved' if approve else 'Rejected'} {request.controller_name}."
                )
                self._page.update()

        return handler

    async def _refresh_instances(self, _event: ft.Event[ft.OutlinedButton]) -> None:
        await self._perform(self._refresh_instances_action)

    async def _refresh_instances_action(self) -> None:
        if self._runtime is None:
            raise OSError("Start the listeners first.")
        snapshots = await self._runtime.list_instances()
        self.instances.controls = [self._instance_row(item) for item in snapshots]

    def _instance_row(self, snapshot: InstanceSnapshot) -> ft.Control:
        return ft.Row(
            [
                ft.Text(snapshot.name, expand=True),
                ft.Text(snapshot.status.value),
                ft.Text(f"PID {snapshot.pid or '-'}"),
                ft.Button(
                    "Task kill",
                    on_click=self._kill_handler(snapshot.instance_id),
                    disabled=snapshot.status is not InstanceStatus.RUNNING,
                ),
            ]
        )

    def _kill_handler(self, instance_id: str) -> Callable[[ft.Event[ft.Button]], Awaitable[None]]:
        async def handler(_event: ft.Event[ft.Button]) -> None:
            async def action() -> None:
                if self._runtime is None:
                    raise OSError("Agent runtime is not running.")
                await self._runtime.stop(instance_id)
                await self._refresh_instances_action()

            await self._perform(action)

        return handler

    async def _perform(self, action: Callable[[], Awaitable[None]]) -> None:
        self.progress.visible = True
        self._page.update()
        try:
            await action()
        except (IdentityError, OSError, ValueError) as error:
            self.status.value = str(error)
        finally:
            self.progress.visible = False
            self._page.update()

    def _load_defaults(self) -> AgentSettings:
        if self._settings_path.is_file():
            try:
                return AgentSettings.from_mapping(
                    json.loads(self._settings_path.read_text(encoding="utf-8"))
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


async def configure_agent_page(page: ft.Page, *, application_data: Path) -> None:
    """Configure and mount the native Agent page."""
    page.title = "ModDebugPilot Agent"
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_700, use_material3=True)
    page.padding = 0
    view = AgentView(page, application_data=application_data)
    page.on_close = lambda _event: asyncio.create_task(view.close())
    page.on_disconnect = lambda _event: asyncio.create_task(view.close())
    page.add(view.build())
