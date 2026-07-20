"""Flet controls that render state and emit typed controller intents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

import flet as ft

from mod_debug_pilot.domain import JobKind, PilotConfig
from mod_debug_pilot.presentation import AppController, AppPhase, AppState

_ACCENT: Final = ft.Colors.BLUE_700
_SUCCESS: Final = ft.Colors.GREEN_700
_FAILURE: Final = ft.Colors.RED_700
_NEUTRAL: Final = ft.Colors.BLUE_GREY_700


@dataclass(frozen=True, slots=True, kw_only=True)
class PhaseAppearance:
    """Accessible visual treatment paired with a textual status."""

    icon: ft.IconData
    color: ft.ColorValue
    label: str


def phase_appearance(*, phase: AppPhase) -> PhaseAppearance:
    """Map every phase to an icon, color, and non-color label."""
    if phase in {AppPhase.LOADING, AppPhase.SAVING, AppPhase.RUNNING}:
        return PhaseAppearance(icon=ft.Icons.PENDING, color=_ACCENT, label="In progress")
    if phase is AppPhase.SUCCEEDED:
        return PhaseAppearance(icon=ft.Icons.CHECK_CIRCLE, color=_SUCCESS, label="Succeeded")
    if phase is AppPhase.FAILED:
        return PhaseAppearance(icon=ft.Icons.ERROR, color=_FAILURE, label="Needs attention")
    if phase is AppPhase.CANCELED:
        return PhaseAppearance(icon=ft.Icons.CANCEL, color=_NEUTRAL, label="Canceled")
    if phase is AppPhase.CLOSED:
        return PhaseAppearance(icon=ft.Icons.LOCK, color=_NEUTRAL, label="Closed")
    return PhaseAppearance(icon=ft.Icons.CHECK, color=_NEUTRAL, label="Ready")


def configure_page(page: ft.Page) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
    """Apply product identity and a desktop-accessible theme."""
    page.title = "ModDebugPilot"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.theme = ft.Theme(color_scheme_seed=_ACCENT, use_material3=True)
    page.padding = 0
    page.bgcolor = ft.Colors.SURFACE


class PilotView:
    """Render one controller state through stable semantic control references."""

    def __init__(self, page: ft.Page, *, controller: AppController) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
        """Create controls without starting I/O or background work."""
        self._page = page
        self._controller = controller
        self._unsubscribe: Callable[[], None] | None = None
        self._rendered_config: PilotConfig | None = None
        self._fields = self._create_fields()
        self.status_icon = ft.Icon(icon=ft.Icons.PENDING, color=_ACCENT, size=28)
        self.status_label = ft.Text("In progress", weight=ft.FontWeight.BOLD)
        self.status_message = ft.Text("Loading configuration…", selectable=True)
        self.progress = ft.ProgressRing(
            width=20,
            height=20,
            stroke_width=2,
            semantics_label="Job in progress",
        )
        self.result_path = ft.Text("No completed run yet.", selectable=True)
        self.save_button = ft.Button(
            "Save settings",
            icon=ft.Icons.SAVE,
            on_click=self._on_save,
        )
        self.validate_button = ft.OutlinedButton(
            "Validate workstation",
            icon=ft.Icons.FACT_CHECK,
            on_click=self._on_validate,
        )
        self.run_button = ft.Button(
            "Run smoke test",
            icon=ft.Icons.ROCKET_LAUNCH,
            on_click=self._on_run,
        )
        self.cancel_button = ft.OutlinedButton(
            "Cancel active job",
            icon=ft.Icons.STOP_CIRCLE,
            on_click=self._on_cancel,
        )
        self._root = self._build_root()

    @property
    def form_fields(self) -> Mapping[str, ft.TextField]:
        """Expose semantic field references for adapter verification."""
        return self._fields

    def build(self) -> ft.Control:
        """Return the stable root control."""
        return self._root

    async def mount(self) -> None:
        """Subscribe rendering, load settings, and bind page shutdown."""
        self._unsubscribe = self._controller.subscribe(listener=self.render)
        self._page.on_close = self._on_page_close
        self._page.on_disconnect = self._on_page_close
        await self._controller.initialize()

    async def unmount(
        self,
    ) -> (
        None
    ):  # keyword-only-exception: AppController listener callbacks receive state positionally.
        """Close owned work and detach rendering exactly once."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self._controller.close()

    def render(self, state: AppState) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
        """Apply one coherent state snapshot and issue one page update."""
        if state.config != self._rendered_config:
            self._set_form_config(config=state.config)
            self._rendered_config = state.config
        for name, field in self._fields.items():
            field.error = state.field_errors.get(name)
        appearance = phase_appearance(phase=state.phase)
        self.status_icon.icon = appearance.icon
        self.status_icon.color = appearance.color
        self.status_label.value = appearance.label
        self.status_message.value = state.message
        busy = state.phase in {AppPhase.LOADING, AppPhase.SAVING, AppPhase.RUNNING}
        running = state.phase is AppPhase.RUNNING
        self.progress.visible = busy
        self.save_button.disabled = busy
        self.validate_button.disabled = busy
        self.run_button.disabled = busy
        self.cancel_button.disabled = not running
        for field in self._fields.values():
            field.disabled = busy
        result = state.latest_result
        self.result_path.value = (
            result.artifact_dir if result is not None else "No completed run yet."
        )
        self._page.update()

    def _create_fields(self) -> dict[str, ft.TextField]:
        return {
            "game_executable": self._path_field(
                label="Game executable",
                hint=r"C:\Program Files (x86)\Steam\steamapps\common\...\Game.exe",
            ),
            "base_profile_dir": self._path_field(
                label="BepInEx base profile",
                hint=r"C:\ModDebugPilot\base-profile",
            ),
            "mod_dll": self._path_field(
                label="Debug mod DLL", hint=r"C:\Project\bin\Debug\Mod.dll"
            ),
            "artifact_root": self._path_field(
                label="Artifact root",
                hint=r"C:\ModDebugPilot\artifacts",
            ),
            "profile_name": ft.TextField(label="Profile name", max_length=64),
            "timeout_seconds": ft.TextField(
                label="Timeout (seconds)",
                keyboard_type=ft.KeyboardType.NUMBER,
            ),
            "screen_width": ft.TextField(
                label="Screen width",
                keyboard_type=ft.KeyboardType.NUMBER,
            ),
            "screen_height": ft.TextField(
                label="Screen height",
                keyboard_type=ft.KeyboardType.NUMBER,
            ),
            "ready_marker": ft.TextField(
                label="BepInEx ready log marker",
                max_length=200,
            ),
            "screenshot_delay_seconds": ft.TextField(
                label="Screenshot delay (seconds)",
                keyboard_type=ft.KeyboardType.NUMBER,
            ),
            "debugger_port": ft.TextField(
                label="Mono debugger port",
                keyboard_type=ft.KeyboardType.NUMBER,
            ),
        }

    @staticmethod
    def _path_field(*, label: str, hint: str) -> ft.TextField:
        return ft.TextField(label=label, hint_text=hint, max_length=4096)

    def _build_root(self) -> ft.Control:
        heading = ft.Container(
            bgcolor=ft.Colors.BLUE_GREY_900,
            padding=ft.Padding.symmetric(horizontal=28, vertical=22),
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.FLIGHT_TAKEOFF, color=ft.Colors.WHITE, size=32),
                    ft.Column(
                        controls=[
                            ft.Text(
                                "ModDebugPilot",
                                color=ft.Colors.WHITE,
                                size=26,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                "Reproducible BepInEx launch and evidence collection",
                                color=ft.Colors.BLUE_GREY_200,
                            ),
                        ],
                        spacing=2,
                    ),
                ],
            ),
        )
        content = ft.ResponsiveRow(
            controls=[
                self._settings_card(),
                ft.Column(
                    col={"sm": 12, "lg": 4},
                    controls=[self._status_card(), self._run_card(), self._artifact_card()],
                    spacing=16,
                ),
            ],
            spacing=16,
            run_spacing=16,
        )
        return ft.Column(
            controls=[
                heading,
                ft.Container(
                    content=content,
                    padding=24,
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    def _settings_card(self) -> ft.Control:
        path_fields = [
            self._fields[name]
            for name in ("game_executable", "base_profile_dir", "mod_dll", "artifact_root")
        ]
        numeric_fields: list[ft.Control] = [
            self._fields[name]
            for name in (
                "profile_name",
                "timeout_seconds",
                "screen_width",
                "screen_height",
                "screenshot_delay_seconds",
                "debugger_port",
            )
        ]
        for field in numeric_fields:
            field.col = {"sm": 12, "md": 6}
        return ft.Card(
            col={"sm": 12, "lg": 8},
            content=ft.Container(
                padding=24,
                content=ft.Column(
                    controls=[
                        ft.Text("Test profile", size=20, weight=ft.FontWeight.BOLD),
                        ft.Text(
                            "Only local files are accepted. The base profile remains read-only.",
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        *path_fields,
                        ft.ResponsiveRow(controls=numeric_fields),
                        self._fields["ready_marker"],
                        ft.Row(controls=[self.save_button], alignment=ft.MainAxisAlignment.END),
                    ],
                    spacing=14,
                ),
            ),
        )

    def _status_card(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    controls=[
                        ft.Text("Status", size=18, weight=ft.FontWeight.BOLD),
                        ft.Row(
                            controls=[self.status_icon, self.status_label, self.progress],
                            spacing=10,
                        ),
                        self.status_message,
                    ],
                ),
            ),
        )

    def _run_card(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    controls=[
                        ft.Text("Actions", size=18, weight=ft.FontWeight.BOLD),
                        ft.Text(
                            "Validate first. Smoke tests temporarily manage two Doorstop files.",
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                        self.validate_button,
                        self.run_button,
                        self.cancel_button,
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ),
        )

    def _artifact_card(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    controls=[
                        ft.Text("Latest artifacts", size=18, weight=ft.FontWeight.BOLD),
                        self.result_path,
                        ft.Text(
                            "request.json • environment.json • result.json • logs • screenshots",
                            size=12,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                ),
            ),
        )

    def _set_form_config(self, *, config: PilotConfig) -> None:
        for name, value in config.to_mapping().items():
            self._fields[name].value = str(value)

    def _form_values(
        self,
    ) -> dict[
        str, object
    ]:  # keyword-only-exception: Flet passes the control event positionally to handlers.
        return {name: field.value for name, field in self._fields.items()}

    async def _on_save(self, event: ft.Event[ft.Button]) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
        del event
        await self._controller.save_settings(values=self._form_values())

    # keyword-only-exception: Flet passes the control event positionally to handlers.
    def _on_validate(self, event: ft.Event[ft.OutlinedButton]) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
        del event
        self._controller.start_job(kind=JobKind.VALIDATE_ENVIRONMENT)

    # keyword-only-exception: Flet passes the control event positionally to handlers.
    def _on_run(self, event: ft.Event[ft.Button]) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
        del event
        self._controller.start_job(kind=JobKind.RUN_SMOKE_TEST)

    # keyword-only-exception: Flet passes the control event positionally to handlers.
    async def _on_cancel(self, event: ft.Event[ft.OutlinedButton]) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
        del event
        await self._controller.cancel_active()

    # keyword-only-exception: Flet passes the page event positionally to handlers.
    async def _on_page_close(self, event: ft.Event[ft.Page]) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet and controller listeners invoke callbacks positionally.
        del event
        await self.unmount()
