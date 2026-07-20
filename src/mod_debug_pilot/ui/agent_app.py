"""Native controlled-workstation Flet adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import flet as ft

from mod_debug_pilot.domain import InstanceSnapshot, InstanceStatus, PairingRequest
from mod_debug_pilot.presentation import AgentController


class AgentView:
    """Render Agent state and emit typed controller actions."""

    def __init__(self, page: ft.Page, *, controller: AgentController) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet callback ABI.
        """Create controls from the controller's initial state."""
        self._page = page
        self._controller = controller
        defaults = controller.state.settings
        self.fields = {
            "agent_name": ft.TextField(label="Agent name", value=defaults.agent_name),
            "bind_host": ft.TextField(label="Bind host", value=defaults.bind_host),
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
        self.status = ft.Text(selectable=True)
        self.controller_url = ft.Text(selectable=True)
        self.pairing_code = ft.Text(selectable=True, size=20)
        self.pending = ft.Column(spacing=8)
        self.instances = ft.Column(spacing=8)
        self.progress = ft.ProgressRing(width=20, height=20, visible=False)
        self.start_button = ft.Button("Start Controller", on_click=self._start)
        self.stop_button = ft.Button("Stop and restore", on_click=self._stop)
        self.open_pairing_button = ft.Button("Open pairing window", on_click=self._open_pairing)
        self._root = self._build()
        self._render()

    def build(self) -> ft.Control:
        """Return the stable root control."""
        return self._root

    async def close(self) -> None:
        """Close the controller-owned host on page shutdown."""
        await self._controller.close()

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
            title="Controller listener",
            controls=[
                ft.Text(
                    "The browser UI uses plain HTTP for trusted private LANs. "
                    "Traffic is not confidential; restrict the port with Windows Firewall.",
                    color=ft.Colors.AMBER_800,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Row([self.start_button, self.stop_button, self.progress]),
                self.status,
                self.controller_url,
            ],
        )
        pairing_card = self._card(
            title="Connection approval",
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
            title="Tracked instances",
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
                                    "Paths and the Web port are stored as non-secret settings.",
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
    def _card(*, title: str, controls: list[ft.Control]) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [ft.Text(title, size=19, weight=ft.FontWeight.BOLD), *controls],
                    spacing=12,
                ),
            )
        )

    async def _start(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        await self._perform(action=self._start_action)

    async def _start_action(self) -> None:
        await self._controller.start(
            values={name: field.value for name, field in self.fields.items()}
        )

    async def _stop(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        await self._perform(action=self._controller.stop)

    async def _open_pairing(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        self._controller.open_pairing()
        self._render_and_update()

    async def _refresh_pending(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.OutlinedButton]
    ) -> None:
        self._controller.refresh_pairings()
        self._render_and_update()

    def _pending_row(self, request: PairingRequest) -> ft.Control:  # noqa: PLR0917 -- keyword-only-exception: Flet callback ABI.
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
                                    on_click=self._decision_handler(request=request, approve=True),
                                ),
                                ft.OutlinedButton(
                                    "Reject",
                                    on_click=self._decision_handler(request=request, approve=False),
                                ),
                            ]
                        ),
                    ]
                ),
            )
        )

    def _decision_handler(
        self,
        *,
        request: PairingRequest,
        approve: bool,
    ) -> Callable[[], Awaitable[None]]:
        async def handler() -> None:
            self._controller.decide_pairing(
                request_id=request.request_id,
                controller_name=request.controller_name,
                approve=approve,
            )
            self._render_and_update()

        return handler

    async def _refresh_instances(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.OutlinedButton]
    ) -> None:
        await self._perform(action=self._controller.refresh_instances)

    def _instance_row(self, *, snapshot: InstanceSnapshot) -> ft.Control:
        return ft.Row(
            [
                ft.Text(snapshot.name, expand=True),
                ft.Text(snapshot.status.value),
                ft.Text(f"PID {snapshot.pid or '-'}"),
                ft.Button(
                    "Task kill",
                    on_click=self._kill_handler(instance_id=snapshot.instance_id),
                    disabled=snapshot.status is not InstanceStatus.RUNNING,
                ),
            ]
        )

    def _kill_handler(
        self, *, instance_id: str
    ) -> Callable[[ft.Event[ft.Button]], Awaitable[None]]:
        async def handler(  # keyword-only-exception: Flet callback ABI.
            _event: ft.Event[ft.Button],
        ) -> None:
            async def action() -> None:
                await self._controller.stop_instance(instance_id=instance_id)

            await self._perform(action=action)

        return handler

    async def _perform(self, *, action: Callable[[], Awaitable[None]]) -> None:
        self.progress.visible = True
        self._page.update()
        try:
            await action()
        except (OSError, ValueError) as error:
            self._controller.fail(message=str(error))
        finally:
            self.progress.visible = False
            self._render_and_update()

    def _render(self) -> None:
        state = self._controller.state
        self.status.value = state.status
        self.controller_url.value = state.controller_url
        self.pairing_code.value = state.pairing_code
        self.pending.controls = [self._pending_row(request=item) for item in state.pending]
        self.instances.controls = [self._instance_row(snapshot=item) for item in state.instances]
        self.start_button.disabled = state.running
        self.stop_button.disabled = not state.running
        self.open_pairing_button.disabled = not state.running
        for field in self.fields.values():
            field.disabled = state.running

    def _render_and_update(self) -> None:
        self._render()
        self._page.update()


async def configure_agent_page(  # noqa: PLR0917 -- keyword-only-exception: Flet callback ABI.
    page: ft.Page,
    *,
    controller: AgentController,
) -> None:
    """Configure and mount the native Agent page."""
    page.title = "ModDebugPilot Agent"
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_700, use_material3=True)
    page.padding = 0
    view = AgentView(page=page, controller=controller)

    async def close_view(  # keyword-only-exception: Flet invokes page callbacks positionally.
        _event: ft.Event[ft.Page],
    ) -> None:
        await view.close()

    page.on_close = close_view
    page.on_disconnect = close_view
    page.add(view.build())
