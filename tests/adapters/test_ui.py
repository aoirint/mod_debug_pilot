"""Semantic tests for the Flet control adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import cast

import flet as ft

from mod_debug_pilot.domain import JobKind, JobOutcome, JobResult, PilotConfig
from mod_debug_pilot.presentation import AppPhase, AppState
from mod_debug_pilot.ui import PilotView, configure_page
from mod_debug_pilot.ui.app import phase_appearance
from tests.unit.test_domain import valid_values
from tests.unit.test_presentation import ConfigRepositoryStub, ExecutorStub, make_controller


class FakePage:
    """Small page surface needed by the view."""

    def __init__(self) -> None:
        """Create an unattached semantic page."""
        self.title = ""
        self.theme_mode: ft.ThemeMode | None = None
        self.theme: ft.Theme | None = None
        self.padding: int | None = None
        self.bgcolor: ft.ColorValue | None = None
        self.on_close: object | None = None
        self.on_disconnect: object | None = None
        self.controls: list[ft.Control] = []
        self.update_count = 0

    def add(  # keyword-only-exception: Flet task callbacks preserve the external signature.
        self, *controls: ft.Control
    ) -> None:
        """Record controls added to the page."""
        self.controls.extend(controls)

    def update(self) -> None:
        """Record one bounded render transaction."""
        self.update_count += 1


def result(*, outcome: JobOutcome = JobOutcome.SUCCEEDED) -> JobResult:
    """Return one deterministic latest result."""
    return JobResult(
        job_id="job",
        outcome=outcome,
        message="complete",
        artifact_dir=r"C:\Artifacts\job",
        started_at="start",
        finished_at="finish",
    )


def test_phase_appearance_covers_every_semantic_variant() -> None:
    """Status uses words and icons in addition to color."""
    expected = {
        AppPhase.LOADING: "In progress",
        AppPhase.SAVING: "In progress",
        AppPhase.RUNNING: "In progress",
        AppPhase.SUCCEEDED: "Succeeded",
        AppPhase.FAILED: "Needs attention",
        AppPhase.CANCELED: "Canceled",
        AppPhase.CLOSED: "Closed",
        AppPhase.READY: "Ready",
    }

    assert {phase: phase_appearance(phase=phase).label for phase in AppPhase} == expected
    assert all(phase_appearance(phase=phase).icon is not None for phase in AppPhase)


def test_configure_page_applies_product_theme() -> None:
    """Page identity and theme are centralized."""
    page = FakePage()

    configure_page(cast(ft.Page, page))

    assert page.title == "ModDebugPilot"
    assert page.theme_mode is ft.ThemeMode.SYSTEM
    assert page.theme is not None
    assert page.theme.use_material3 is True
    assert page.padding == 0


def test_view_mount_render_and_unmount_are_lifecycle_owned() -> None:
    """Mount loads state, render updates semantic controls, and unmount detaches."""

    async def scenario() -> None:
        config = PilotConfig.from_mapping(values=valid_values())
        controller = make_controller(repository=ConfigRepositoryStub(value=config))
        page = FakePage()
        view = PilotView(page=cast(ft.Page, page), controller=controller)
        page.add(view.build())

        await view.mount()

        assert view.form_fields["profile_name"].value == "smoke_1"
        assert view.status_label.value == "Ready"
        assert view.progress.visible is False
        assert view.cancel_button.disabled is True
        assert page.update_count > 0
        assert page.on_close is not None
        assert page.on_disconnect is not None

        failed = replace(
            controller.state,
            phase=AppPhase.FAILED,
            message="Fix settings.",
            field_errors={"game_executable": "Required."},
            latest_result=result(outcome=JobOutcome.FAILED),
        )
        view.render(state=failed)
        assert view.form_fields["game_executable"].error == "Required."
        assert view.result_path.value == r"C:\Artifacts\job"
        assert view.status_label.value == "Needs attention"

        await view.unmount()
        await view.unmount()
        assert controller.state.phase.value == "closed"

    asyncio.run(scenario())


def test_view_actions_emit_save_validate_and_run_intents() -> None:
    """Controls convert values once and invoke named controller operations."""

    async def scenario() -> None:
        controller = make_controller(executor=ExecutorStub(outcome=JobOutcome.SUCCEEDED))
        view = PilotView(page=cast(ft.Page, FakePage()), controller=controller)
        await view.mount()
        for name, value in valid_values().items():
            view.form_fields[name].value = str(value)

        save = cast(
            Callable[[ft.Event[ft.Button]], Awaitable[None]],
            view.save_button.on_click,
        )
        await save(ft.Event("click", view.save_button))
        assert controller.state.config.profile_name == "smoke_1"

        validate = cast(
            Callable[[ft.Event[ft.OutlinedButton]], None],
            view.validate_button.on_click,
        )
        validate(ft.Event("click", view.validate_button))
        await controller.wait_for_idle()
        assert controller.state.latest_result is not None

        run = cast(Callable[[ft.Event[ft.Button]], None], view.run_button.on_click)
        run(ft.Event("click", view.run_button))
        await controller.wait_for_idle()
        assert controller.state.latest_result is not None
        assert controller.state.latest_result.outcome is JobOutcome.SUCCEEDED

    asyncio.run(scenario())


def test_view_cancel_and_page_close_handlers_cleanup() -> None:
    """Cancel and both page shutdown events await the controller owner."""

    async def scenario() -> None:
        gate = asyncio.Event()
        controller = make_controller(executor=ExecutorStub(gate=gate))
        page = FakePage()
        view = PilotView(page=cast(ft.Page, page), controller=controller)
        await view.mount()

        run = cast(Callable[[ft.Event[ft.Button]], None], view.run_button.on_click)
        run(ft.Event("click", view.run_button))
        await asyncio.sleep(0)
        assert controller.state.active_job is JobKind.RUN_SMOKE_TEST
        assert view.cancel_button.disabled is False

        cancel = cast(
            Callable[[ft.Event[ft.OutlinedButton]], Awaitable[None]],
            view.cancel_button.on_click,
        )
        await cancel(ft.Event("click", view.cancel_button))
        assert controller.state.phase is AppPhase.CANCELED

        close = cast(
            Callable[[ft.Event[ft.Page]], Awaitable[None]],
            page.on_close,
        )
        disconnect = cast(
            Callable[[ft.Event[ft.Page]], Awaitable[None]],
            page.on_disconnect,
        )
        await close(ft.Event("close", cast(ft.Page, page)))
        await disconnect(ft.Event("disconnect", cast(ft.Page, page)))
        assert controller.state.phase.value == "closed"

    asyncio.run(scenario())


def test_render_disables_form_for_every_busy_phase() -> None:
    """Loading, saving, and running serialize actions with distinct cancellation."""
    page = FakePage()
    view = PilotView(page=cast(ft.Page, page), controller=make_controller())
    base = AppState.initial()

    for phase in (AppPhase.LOADING, AppPhase.SAVING, AppPhase.RUNNING):
        view.render(state=replace(base, phase=phase))
        assert view.save_button.disabled is True
        assert all(field.disabled for field in view.form_fields.values())
        assert view.cancel_button.disabled is (phase is not AppPhase.RUNNING)

    view.render(state=replace(base, phase=AppPhase.READY))
    assert view.save_button.disabled is False
    assert view.result_path.value == "No completed run yet."
