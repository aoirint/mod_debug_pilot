"""Lifecycle owner for application state and asynchronous jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace

from mod_debug_pilot.application.services import JobService, SettingsService
from mod_debug_pilot.domain.models import JobKind, JobOutcome, ValidationError
from mod_debug_pilot.presentation.models import AppPhase, AppState

StateListener = Callable[[AppState], None]


class AppController:
    """Own UI state, one active task, cancellation, and stale-result rejection."""

    def __init__(self, *, settings: SettingsService, jobs: JobService) -> None:
        """Create an inactive page-session controller."""
        self._settings = settings
        self._jobs = jobs
        self._state = AppState.initial()
        self._listeners: list[StateListener] = []
        self._active_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._closed = False

    @property
    def state(self) -> AppState:
        """Return the latest immutable state."""
        return self._state

    def subscribe(self, *, listener: StateListener) -> Callable[[], None]:
        """Register a renderer and return an idempotent unsubscribe callback."""
        self._listeners.append(listener)
        listener(self._state)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if subscribed:
                self._listeners.remove(listener)
                subscribed = False

        return unsubscribe

    async def initialize(self) -> None:
        """Load configuration once for the page session."""
        if self._closed:
            return
        try:
            config = await self._settings.load()
        except (OSError, ValidationError):
            self._commit(
                state=replace(
                    self._state,
                    phase=AppPhase.FAILED,
                    message="Saved configuration could not be loaded. Review or reset it.",
                ),
            )
            return
        self._commit(
            state=replace(
                self._state,
                phase=AppPhase.READY,
                config=config,
                message="Ready. Validate the workstation before running a test.",
                field_errors={},
            ),
        )

    async def save_settings(self, *, values: dict[str, object]) -> bool:
        """Validate and persist one form submission."""
        if self._closed or self._active_task is not None:
            return False
        self._commit(state=replace(self._state, phase=AppPhase.SAVING, message="Saving…"))
        try:
            config = await self._settings.save(values=values)
        except ValidationError as error:
            self._commit(
                state=replace(
                    self._state,
                    phase=AppPhase.FAILED,
                    message="Correct the highlighted settings.",
                    field_errors=error.errors,
                ),
            )
            return False
        except OSError:
            self._commit(
                state=replace(
                    self._state,
                    phase=AppPhase.FAILED,
                    message="Configuration could not be saved.",
                    field_errors={},
                ),
            )
            return False
        self._commit(
            state=replace(
                self._state,
                phase=AppPhase.READY,
                config=config,
                message="Configuration saved.",
                field_errors={},
            ),
        )
        return True

    def start_job(self, *, kind: JobKind) -> bool:
        """Start one owned task without blocking the Flet event handler."""
        if self._closed or self._active_task is not None:
            return False
        self._generation += 1
        generation = self._generation
        self._commit(
            state=replace(
                self._state,
                phase=AppPhase.RUNNING,
                message=f"Running {kind.value}…",
                active_job=kind,
                field_errors={},
            ),
        )
        self._active_task = asyncio.create_task(
            self._run_job(kind=kind, generation=generation),
            name=f"moddebugpilot:{kind.value}:{generation}",
        )
        return True

    async def cancel_active(self) -> bool:
        """Cancel and await the active task, then publish a truthful state."""
        task = self._active_task
        if task is None:
            return False
        self._generation += 1
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._commit(
            state=replace(
                self._state,
                phase=AppPhase.CANCELED,
                message="Job canceled. Partial artifacts were preserved.",
                active_job=None,
            ),
        )
        return True

    async def wait_for_idle(self) -> None:
        """Await the currently active job for tests and orderly shutdown."""
        task = self._active_task
        if task is not None:
            await task

    async def close(self) -> None:
        """Cancel owned work and close the controller exactly once."""
        if self._closed:
            return
        await self.cancel_active()
        self._closed = True
        self._commit(
            state=replace(
                self._state,
                phase=AppPhase.CLOSED,
                message="Closed.",
                active_job=None,
            ),
        )
        self._listeners.clear()

    async def _run_job(self, *, kind: JobKind, generation: int) -> None:
        try:
            result = await self._jobs.run(kind=kind, config=self._state.config)
            if generation != self._generation or self._closed:
                return
            phase = phase_for_outcome(outcome=result.outcome)
            self._commit(
                state=replace(
                    self._state,
                    phase=phase,
                    message=result.message,
                    active_job=None,
                    latest_result=result,
                ),
            )
        except asyncio.CancelledError:
            raise
        except OSError:
            if generation == self._generation and not self._closed:
                self._commit(
                    state=replace(
                        self._state,
                        phase=AppPhase.FAILED,
                        message="The runner could not complete the job.",
                        active_job=None,
                    ),
                )
        finally:
            self._active_task = None

    def _commit(self, *, state: AppState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener(state)


def phase_for_outcome(*, outcome: JobOutcome) -> AppPhase:
    """Map every runner outcome to a terminal presentation phase."""
    if outcome is JobOutcome.SUCCEEDED:
        return AppPhase.SUCCEEDED
    if outcome is JobOutcome.CANCELED:
        return AppPhase.CANCELED
    return AppPhase.FAILED
