"""Agent-hosted Flet Web controller for browser-only workstations."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import flet as ft

from mod_debug_pilot.domain import InstanceSnapshot, InstanceSpec, InstanceStatus
from mod_debug_pilot.infrastructure.agent_runtime import RemoteAgentRuntime
from mod_debug_pilot.infrastructure.profiles import (
    ImportedProfile,
    ProfileImportError,
    ProfileWorkspace,
    ThunderstoreProfileImporter,
)
from mod_debug_pilot.infrastructure.security import (
    AuthenticationError,
    PairingBroker,
    PairingRequest,
    create_ephemeral_controller_identity,
)

_SAFE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_LOCAL_MOD: Final = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class WebControllerContext:
    """Agent-owned services shared by independent browser page sessions."""

    pairing: PairingBroker
    importer: ThunderstoreProfileImporter
    workspace: ProfileWorkspace
    runtime: RemoteAgentRuntime
    data_root: Path


class WebControllerView:
    """Render a controller session that has no local Python installation."""

    def __init__(self, page: ft.Page, *, context: WebControllerContext) -> None:
        """Create one initially unauthorized browser session."""
        self._page = page
        self._context = context
        self._pairing: PairingRequest | None = None
        self._approved = False
        self._local_mod_name = ""
        self._local_mod_bytes: bytes | None = None
        self._draft: Path | None = None
        self._imported: ImportedProfile | None = None
        self._picker = ft.FilePicker()
        self.status = ft.Text("Enter the Agent-displayed pairing code.", selectable=True)
        self.controller_name = ft.TextField(label="Controller name", value="Browser controller")
        self.pairing_code = ft.TextField(label="One-time pairing code", password=True)
        self.profile_code = ft.TextField(label="Thunderstore Profile Code")
        self.profile_id = ft.TextField(label="Profile ID", value="debug-profile")
        self.local_mod = ft.Text("No local DLL selected.", selectable=True)
        self.config_selector = ft.Dropdown(label="Mod configuration", disabled=True)
        self.config_editor = ft.TextField(
            label="Configuration contents (UTF-8)",
            multiline=True,
            min_lines=12,
            max_lines=24,
            disabled=True,
        )
        self.instance_name = ft.TextField(label="Instance name", value="client-1")
        self.debugger_port = ft.TextField(label="Debugger port", value="55555")
        self.instances = ft.Column(spacing=8)
        self.busy = ft.ProgressRing(width=20, height=20, visible=False)
        self._privileged_controls: list[ft.Control] = []
        self._root = self._build()

    def build(self) -> ft.Control:
        """Return the stable page root."""
        return self._root

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
        for control in self._privileged_controls:
            control.disabled = True
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
                    "Connection approval",
                    controls=[
                        self.controller_name,
                        self.pairing_code,
                        ft.Row([pair_button, check_button, self.busy]),
                        self.status,
                    ],
                ),
                self._card(
                    "Thunderstore profile and local build",
                    controls=[
                        self.profile_code,
                        self.profile_id,
                        ft.Row([choose_button, self.local_mod]),
                        import_button,
                    ],
                ),
                self._card(
                    "Mod configuration editor",
                    controls=[
                        self.config_selector,
                        ft.Row([load_config, save_config, install_button]),
                        self.config_editor,
                    ],
                ),
                self._card(
                    "Instances",
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

    async def _request_pairing(self, _event: ft.Event[ft.Button]) -> None:
        await self._perform(self._request_pairing_action)

    async def _request_pairing_action(self) -> None:
        identity = create_ephemeral_controller_identity(self.controller_name.value or "")
        self._pairing = self._context.pairing.request(
            code=self.pairing_code.value or "",
            controller_id=identity.controller_id,
            controller_name=identity.name,
            public_key_b64=identity.public_key_b64,
            persist_authorization=False,
        )
        self.status.value = "Connection requested. Approve it in the Agent native GUI."

    async def _check_approval(self, _event: ft.Event[ft.OutlinedButton]) -> None:
        await self._perform(self._check_approval_action)

    async def _check_approval_action(self) -> None:
        if self._pairing is None:
            raise AuthenticationError("Request a connection first.")
        status = self._context.pairing.status(
            self._pairing.request_id,
            poll_token=self._pairing.poll_token,
        )
        if status is None:
            self.status.value = "Still waiting for Agent approval."
            return
        if not status:
            self.status.value = "The Agent operator rejected this session."
            return
        self._approved = True
        for control in self._privileged_controls:
            control.disabled = False
        self.config_selector.disabled = True
        self.config_editor.disabled = True
        self.status.value = "Approved. This browser session can now control the Agent."

    async def _choose_mod(self, _event: ft.Event[ft.OutlinedButton]) -> None:
        if not self._approved:
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
            self.status.value = "The selected DLL could not be read or exceeds 64 MiB."
        else:
            self._local_mod_name = Path(selected.name).name
            self._local_mod_bytes = raw
            self.local_mod.value = f"{self._local_mod_name} ({len(raw):,} bytes)"
        self._page.update()

    async def _import_profile(self, _event: ft.Event[ft.Button]) -> None:
        await self._perform(self._import_profile_action)

    async def _import_profile_action(self) -> None:
        self._require_approved()
        profile_id = self._validated_profile_id()
        if self._local_mod_bytes is None:
            raise ProfileImportError("Select a locally built DLL first.")
        imported = await self._context.importer.import_code(self.profile_code.value or "")
        draft = self._context.data_root / "controller-drafts" / profile_id
        if draft.exists():
            raise ProfileImportError("A draft with this Profile ID already exists.")
        upload_dir = self._context.data_root / "controller-uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        local_mod = upload_dir / f"{profile_id}-{self._local_mod_name}"
        local_mod.write_bytes(self._local_mod_bytes)
        try:
            await self._context.workspace.materialize(
                imported,
                destination=draft,
                local_mod=local_mod,
            )
        finally:
            local_mod.unlink(missing_ok=True)
        self._draft = draft
        self._imported = imported
        configs = self._context.workspace.config_files(draft)
        root = draft / "BepInEx" / "config"
        options = [
            ft.DropdownOption(key=path.relative_to(root).as_posix(), text=path.name)
            for path in configs
        ]
        self.config_selector.options = options
        self.config_selector.disabled = not options
        self.config_editor.disabled = not options
        self.status.value = (
            f"Draft prepared with {len(imported.mods)} declared mods and {len(configs)} configs."
        )

    async def _load_config(self, _event: ft.Event[ft.OutlinedButton]) -> None:
        await self._perform(self._load_config_action)

    async def _load_config_action(self) -> None:
        draft, relative = self._selected_config()
        self.config_editor.value = self._context.workspace.read_config(draft, relative=relative)
        self.status.value = f"Loaded {relative}."

    async def _save_config(self, _event: ft.Event[ft.Button]) -> None:
        await self._perform(self._save_config_action)

    async def _save_config_action(self) -> None:
        draft, relative = self._selected_config()
        self._context.workspace.write_config(
            draft,
            relative=relative,
            content=self.config_editor.value or "",
        )
        self.status.value = f"Saved {relative}."

    async def _install_profile(self, _event: ft.Event[ft.Button]) -> None:
        await self._perform(self._install_profile_action)

    async def _install_profile_action(self) -> None:
        self._require_approved()
        if self._draft is None or self._imported is None:
            raise ProfileImportError("Prepare a profile draft first.")
        profile_id = self._validated_profile_id()
        bundles = self._context.data_root / "controller-bundles"
        bundle = bundles / f"{profile_id}.mdp-profile"
        manifest = await asyncio.to_thread(
            self._context.workspace.create_bundle,
            self._draft,
            profile_name=profile_id,
            source_mods=(mod.dependency for mod in self._imported.mods),
            destination=bundle,
        )
        installed_name = await self._context.runtime.install_profile(
            profile_id,
            bundle=bundle.read_bytes(),
        )
        self.status.value = f"Installed {installed_name}: {len(manifest.files)} verified files."
        shutil.rmtree(self._draft)
        self._draft = None

    async def _launch(self, _event: ft.Event[ft.Button]) -> None:
        await self._perform(self._launch_action)

    async def _launch_action(self) -> None:
        self._require_approved()
        spec = InstanceSpec(
            name=self.instance_name.value or "",
            profile_id=self._validated_profile_id(),
            debugger_port=int(self.debugger_port.value or ""),
        )
        await self._context.runtime.launch(spec)
        await self._refresh_action()
        self._increment_instance_fields(spec)

    async def _refresh(self, _event: ft.Event[ft.OutlinedButton]) -> None:
        await self._perform(self._refresh_action)

    async def _refresh_action(self) -> None:
        self._require_approved()
        snapshots = await self._context.runtime.list_instances()
        self.instances.controls = [self._instance_row(item) for item in snapshots]
        self.status.value = f"Loaded {len(snapshots)} instance records."

    def _instance_row(self, snapshot: InstanceSnapshot) -> ft.Control:
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
                            on_click=self._capture_handler(snapshot.instance_id),
                            disabled=snapshot.status is not InstanceStatus.RUNNING,
                            col=2,
                        ),
                        ft.Button(
                            "Task kill",
                            on_click=self._stop_handler(snapshot.instance_id),
                            disabled=snapshot.status is not InstanceStatus.RUNNING,
                            col=2,
                        ),
                    ]
                ),
            )
        )

    def _capture_handler(self, instance_id: str) -> Callable[[], Awaitable[None]]:
        async def handler() -> None:
            async def action() -> None:
                path = await self._context.runtime.capture(instance_id)
                await self._picker.save_file(file_name=path.name, src_bytes=path.read_bytes())
                self.status.value = f"Downloaded screenshot {path.name}."

            await self._perform(action)

        return handler

    def _stop_handler(self, instance_id: str) -> Callable[[], Awaitable[None]]:
        async def handler() -> None:
            async def action() -> None:
                await self._context.runtime.stop(instance_id)
                await self._refresh_action()

            await self._perform(action)

        return handler

    async def _perform(self, action: Callable[[], Awaitable[None]]) -> None:
        self.busy.visible = True
        self._page.update()
        try:
            await action()
        except (AuthenticationError, ProfileImportError, OSError, ValueError) as error:
            self.status.value = str(error)
        finally:
            self.busy.visible = False
            self._page.update()

    def _require_approved(self) -> None:
        if not self._approved:
            raise AuthenticationError("This browser session is not approved.")

    def _validated_profile_id(self) -> str:
        value = (self.profile_id.value or "").strip()
        if _SAFE_ID.fullmatch(value) is None:
            raise ProfileImportError(
                "Profile ID must use letters, numbers, dot, hyphen, or underscore."
            )
        return value

    def _selected_config(self) -> tuple[Path, str]:
        if self._draft is None or not self.config_selector.value:
            raise ProfileImportError("Select a configuration file.")
        return self._draft, self.config_selector.value

    def _increment_instance_fields(self, spec: InstanceSpec) -> None:
        match = re.search(r"(\d+)$", spec.name)
        if match:
            number = int(match.group(1)) + 1
            self.instance_name.value = spec.name[: match.start(1)] + str(number)
        self.debugger_port.value = str(spec.debugger_port + 1)


async def configure_web_controller(page: ft.Page, *, context: WebControllerContext) -> None:
    """Configure and mount one browser controller page."""
    page.title = "ModDebugPilot Controller"
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_700, use_material3=True)
    page.padding = 0
    view = WebControllerView(page, context=context)
    page.add(view.build())
