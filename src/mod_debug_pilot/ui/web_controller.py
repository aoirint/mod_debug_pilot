"""Agent-hosted Flet Web controller adapter."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

import flet as ft

from mod_debug_pilot.domain import InstanceSnapshot, InstanceSpec, InstanceStatus
from mod_debug_pilot.presentation import BrowserController

_MAX_LOCAL_MOD: Final = 64 * 1024 * 1024


class WebControllerView:
    """Render one browser session and emit controller actions."""

    def __init__(self, page: ft.Page, *, controller: BrowserController) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet callback ABI.
        """Create one initially unauthorized browser view."""
        self._page = page
        self._controller = controller
        self._local_mod_name = ""
        self._local_mod_bytes: bytes | None = None
        self._picker = ft.FilePicker()
        self.status = ft.Text(selectable=True)
        self.controller_name = ft.TextField(label="Controller name", value="Browser controller")
        self.pairing_code = ft.TextField(label="One-time pairing code", password=True)
        self.profile_code = ft.TextField(label="Thunderstore Profile Code")
        self.profile_id = ft.TextField(label="Profile ID", value="debug-profile")
        self.local_mod = ft.Text("No local DLL selected.", selectable=True)
        self.config_selector = ft.Dropdown(label="Mod configuration")
        self.config_editor = ft.TextField(
            label="Configuration contents (UTF-8)",
            multiline=True,
            min_lines=12,
            max_lines=24,
        )
        self.instance_name = ft.TextField(label="Instance name", value="client-1")
        self.debugger_port = ft.TextField(label="Debugger port", value="55555")
        self.instances = ft.Column(spacing=8)
        self.busy = ft.ProgressRing(width=20, height=20, visible=False)
        self._privileged_controls: list[ft.Control] = []
        self._root = self._build()
        self._render()

    def build(self) -> ft.Control:
        """Return the stable page root."""
        return self._root

    async def close(self) -> None:
        """Discard unfinished state owned by this browser page."""
        await self._controller.close()

    def _build(self) -> ft.Control:
        pair_button = ft.Button("Request connection", on_click=self._request_pairing)
        check_button = ft.OutlinedButton("Check approval", on_click=self._check_approval)
        choose_button = ft.OutlinedButton("Choose local DLL", on_click=self._choose_mod)
        import_button = ft.Button("Import and prepare draft", on_click=self._import_profile)
        load_config = ft.OutlinedButton("Load config", on_click=self._load_config)
        save_config = ft.Button("Save config", on_click=self._save_config)
        install_button = ft.Button("Install profile on Agent", on_click=self._install_profile)
        launch_button = ft.Button("Launch instance", on_click=self._launch)
        refresh_button = ft.OutlinedButton("Refresh instances", on_click=self._refresh)
        self._privileged_controls = [
            choose_button,
            import_button,
            load_config,
            save_config,
            install_button,
            launch_button,
            refresh_button,
            self.profile_code,
            self.profile_id,
            self.config_selector,
            self.config_editor,
            self.instance_name,
            self.debugger_port,
        ]
        header = ft.Container(
            bgcolor=ft.Colors.BLUE_GREY_900,
            padding=ft.Padding.symmetric(horizontal=28, vertical=20),
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LAN, color=ft.Colors.WHITE, size=30),
                    ft.Column(
                        controls=[
                            ft.Text(
                                "ModDebugPilot Controller",
                                color=ft.Colors.WHITE,
                                size=24,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                "Agent-hosted trusted-LAN HTTP session",
                                color=ft.Colors.BLUE_GREY_200,
                            ),
                        ],
                        spacing=2,
                    ),
                ]
            ),
        )
        content = ft.Column(
            controls=[
                ft.Card(
                    bgcolor=ft.Colors.AMBER_50,
                    content=ft.Container(
                        padding=16,
                        content=ft.Text(
                            "HTTP removes certificate setup, but it does not encrypt this "
                            "browser session. Use only on a private trusted LAN and keep the "
                            "Agent Web port blocked from other networks.",
                            color=ft.Colors.AMBER_900,
                            weight=ft.FontWeight.BOLD,
                        ),
                    ),
                ),
                self._card(
                    title="Connection approval",
                    controls=[
                        self.controller_name,
                        self.pairing_code,
                        ft.Row([pair_button, check_button, self.busy]),
                        self.status,
                    ],
                ),
                self._card(
                    title="Thunderstore profile and local build",
                    controls=[
                        self.profile_code,
                        self.profile_id,
                        ft.Row([choose_button, self.local_mod]),
                        import_button,
                    ],
                ),
                self._card(
                    title="Mod configuration editor",
                    controls=[
                        self.config_selector,
                        ft.Row([load_config, save_config, install_button]),
                        self.config_editor,
                    ],
                ),
                self._card(
                    title="Instances",
                    controls=[
                        ft.ResponsiveRow(
                            [
                                self.instance_name,
                                self.debugger_port,
                                launch_button,
                                refresh_button,
                            ]
                        ),
                        ft.Text(
                            "Normal saves are protected. Each debug instance receives a "
                            "distinct isolated save area.",
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.instances,
                    ],
                ),
            ],
            spacing=16,
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

    async def _request_pairing(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        await self._perform(action=self._request_pairing_action)

    async def _request_pairing_action(self) -> None:
        self._controller.request_pairing(
            code=self.pairing_code.value or "",
            controller_name=self.controller_name.value or "",
        )

    async def _check_approval(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.OutlinedButton]
    ) -> None:
        await self._perform(action=self._check_approval_action)

    async def _check_approval_action(self) -> None:
        self._controller.check_pairing()

    async def _choose_mod(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.OutlinedButton]
    ) -> None:
        if not self._controller.state.approved:
            return
        files = await self._picker.pick_files(
            dialog_title="Select locally built mod DLL",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["dll"],
            with_data=True,
        )
        if not files:
            return
        selected = files[0]
        raw = getattr(selected, "bytes", None)
        if not isinstance(raw, bytes) or len(raw) > _MAX_LOCAL_MOD:
            self._controller.fail(message="The selected DLL could not be read or exceeds 64 MiB.")
        else:
            self._local_mod_name = Path(selected.name).name
            self._local_mod_bytes = raw
            self.local_mod.value = f"{self._local_mod_name} ({len(raw):,} bytes)"
        self._render_and_update()

    async def _import_profile(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        await self._perform(action=self._import_profile_action)

    async def _import_profile_action(self) -> None:
        if self._local_mod_bytes is None:
            raise ValueError("Select a locally built DLL first.")
        await self._controller.prepare_profile(
            profile_id=self.profile_id.value or "",
            profile_code=self.profile_code.value or "",
            local_mod_name=self._local_mod_name,
            local_mod_bytes=self._local_mod_bytes,
        )

    async def _load_config(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.OutlinedButton]
    ) -> None:
        await self._perform(action=self._load_config_action)

    async def _load_config_action(self) -> None:
        self._controller.load_config(relative=self._selected_config())

    async def _save_config(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        await self._perform(action=self._save_config_action)

    async def _save_config_action(self) -> None:
        self._controller.save_config(
            relative=self._selected_config(),
            content=self.config_editor.value or "",
        )

    async def _install_profile(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        await self._perform(action=self._install_profile_action)

    async def _install_profile_action(self) -> None:
        await self._controller.install_profile(profile_id=self.profile_id.value or "")

    async def _launch(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.Button]
    ) -> None:
        await self._perform(action=self._launch_action)

    async def _launch_action(self) -> None:
        spec = await self._controller.launch(
            name=self.instance_name.value or "",
            profile_id=self.profile_id.value or "",
            debugger_port=self.debugger_port.value or "",
        )
        self._increment_instance_fields(spec=spec)

    async def _refresh(  # keyword-only-exception: Flet callback ABI.
        self, _event: ft.Event[ft.OutlinedButton]
    ) -> None:
        await self._perform(action=self._controller.refresh_instances)

    def _instance_row(self, *, snapshot: InstanceSnapshot) -> ft.Control:
        color = (
            ft.Colors.GREEN_700
            if snapshot.status is InstanceStatus.RUNNING
            else ft.Colors.BLUE_GREY_600
        )
        return ft.Card(
            content=ft.Container(
                padding=12,
                content=ft.ResponsiveRow(
                    [
                        ft.Text(snapshot.name, weight=ft.FontWeight.BOLD, col=3),
                        ft.Text(snapshot.status.value, color=color, col=2),
                        ft.Text(f"PID {snapshot.pid or '-'}", col=2),
                        ft.OutlinedButton(
                            "Screenshot",
                            on_click=self._capture_handler(instance_id=snapshot.instance_id),
                            disabled=snapshot.status is not InstanceStatus.RUNNING,
                            col=2,
                        ),
                        ft.Button(
                            "Task kill",
                            on_click=self._stop_handler(instance_id=snapshot.instance_id),
                            disabled=snapshot.status is not InstanceStatus.RUNNING,
                            col=2,
                        ),
                    ]
                ),
            )
        )

    def _capture_handler(self, *, instance_id: str) -> Callable[[], Awaitable[None]]:
        async def handler() -> None:
            async def action() -> None:
                artifact = await self._controller.capture(instance_id=instance_id)
                await self._picker.save_file(
                    file_name=artifact.name,
                    src_bytes=artifact.content,
                )

            await self._perform(action=action)

        return handler

    def _stop_handler(self, *, instance_id: str) -> Callable[[], Awaitable[None]]:
        async def handler() -> None:
            async def action() -> None:
                await self._controller.stop_instance(instance_id=instance_id)

            await self._perform(action=action)

        return handler

    async def _perform(self, *, action: Callable[[], Awaitable[None]]) -> None:
        self.busy.visible = True
        self._page.update()
        try:
            await action()
        except (OSError, ValueError) as error:
            self._controller.fail(message=str(error))
        finally:
            self.busy.visible = False
            self._render_and_update()

    def _selected_config(self) -> str:
        if not self.config_selector.value:
            raise ValueError("Select a configuration file.")
        return self.config_selector.value

    def _increment_instance_fields(self, *, spec: InstanceSpec) -> None:
        match = re.search(r"(\d+)$", spec.name)
        if match:
            number = int(match.group(1)) + 1
            self.instance_name.value = spec.name[: match.start(1)] + str(number)
        self.debugger_port.value = str(spec.debugger_port + 1)

    def _render(self) -> None:
        state = self._controller.state
        self.status.value = state.status
        self.config_selector.options = [
            ft.DropdownOption(key=relative, text=Path(relative).name)
            for relative in state.config_files
        ]
        self.config_editor.value = state.config_content
        self.instances.controls = [self._instance_row(snapshot=item) for item in state.instances]
        for control in self._privileged_controls:
            control.disabled = not state.approved
        configs_available = state.approved and bool(state.config_files)
        self.config_selector.disabled = not configs_available
        self.config_editor.disabled = not configs_available

    def _render_and_update(self) -> None:
        self._render()
        self._page.update()


async def configure_web_controller(  # noqa: PLR0917 -- keyword-only-exception: Flet callback ABI.
    page: ft.Page,
    *,
    controller: BrowserController,
) -> None:
    """Configure and mount one browser controller page."""
    page.title = "ModDebugPilot Controller"
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_700, use_material3=True)
    page.padding = 0
    view = WebControllerView(page=page, controller=controller)

    async def close_view(  # keyword-only-exception: Flet invokes page callbacks positionally.
        _event: ft.Event[ft.Page],
    ) -> None:
        await view.close()

    page.on_close = close_view
    page.on_disconnect = close_view
    page.add(view.build())
