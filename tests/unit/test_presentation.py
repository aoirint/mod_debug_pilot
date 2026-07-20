"""Tests for page-session state, tasks, cancellation, and stale completion."""

from __future__ import annotations

import asyncio

from mod_debug_pilot.application.services import JobService, SettingsService
from mod_debug_pilot.domain import (
    JobKind,
    JobOutcome,
    JobRequest,
    JobResult,
    PilotConfig,
    ValidationError,
)
from mod_debug_pilot.presentation.controller import AppController, phase_for_outcome
from mod_debug_pilot.presentation.models import AppPhase, AppState
from tests.unit.test_domain import valid_values


class ConfigRepositoryStub:
    """Controllable settings adapter."""

    def __init__(
        self,
        value: PilotConfig | None = None,
        *,
        load_error: Exception | None = None,
        save_error: Exception | None = None,
    ) -> None:
        """Configure load and save behavior."""
        self.value = value
        self.load_error = load_error
        self.save_error = save_error

    async def load(self) -> PilotConfig | None:
        """Return or fail the load."""
        if self.load_error is not None:
            raise self.load_error
        return self.value

    async def save(self, config: PilotConfig) -> None:
        """Save or fail the write."""
        if self.save_error is not None:
            raise self.save_error
        self.value = config


class RequestFactoryStub:
    """Deterministic request factory."""

    def create(self, kind: JobKind, *, config: PilotConfig) -> JobRequest:
        """Create a fixed request."""
        return JobRequest(job_id="job", kind=kind, created_at="now", config=config)


class ExecutorStub:
    """Controllable asynchronous executor."""

    def __init__(
        self,
        outcome: JobOutcome = JobOutcome.SUCCEEDED,
        *,
        error: OSError | None = None,
        gate: asyncio.Event | None = None,
        ignore_cancellation: bool = False,
    ) -> None:
        """Configure completion, blocking, and cancellation behavior."""
        self.outcome = outcome
        self.error = error
        self.gate = gate
        self.ignore_cancellation = ignore_cancellation

    async def execute(self, request: JobRequest) -> JobResult:
        """Wait when requested and then complete or fail."""
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                if not self.ignore_cancellation:
                    raise
        if self.error is not None:
            raise self.error
        return JobResult(
            job_id=request.job_id,
            outcome=self.outcome,
            message=self.outcome.value,
            artifact_dir="artifacts/job",
            started_at="start",
            finished_at="finish",
        )


def make_controller(
    *,
    repository: ConfigRepositoryStub | None = None,
    executor: ExecutorStub | None = None,
) -> AppController:
    """Compose a controller from deterministic stubs."""
    settings = SettingsService(repository or ConfigRepositoryStub())
    jobs = JobService(executor or ExecutorStub(), request_factory=RequestFactoryStub())
    return AppController(settings, jobs=jobs)


def test_initial_state_and_subscription_are_explicit() -> None:
    """A subscriber receives the current snapshot and can unsubscribe twice."""
    state = AppState.initial()
    controller = make_controller()
    received: list[AppState] = []

    unsubscribe = controller.subscribe(received.append)
    unsubscribe()
    unsubscribe()

    assert state.phase is AppPhase.LOADING
    assert state.message == "Loading configuration…"
    assert received == [controller.state]


def test_initialize_success_and_failures() -> None:
    """Loading maps saved, corrupt, and unreadable configuration truthfully."""
    config = PilotConfig.from_mapping(valid_values())
    success = make_controller(repository=ConfigRepositoryStub(config))
    io_failure = make_controller(repository=ConfigRepositoryStub(load_error=OSError()))
    invalid_failure = make_controller(
        repository=ConfigRepositoryStub(load_error=ValidationError({"file": "bad"})),
    )
    received: list[AppState] = []
    success.subscribe(received.append)

    asyncio.run(success.initialize())
    asyncio.run(io_failure.initialize())
    asyncio.run(invalid_failure.initialize())

    assert success.state.phase is AppPhase.READY
    assert success.state.config == config
    assert [state.phase for state in received] == [AppPhase.LOADING, AppPhase.READY]
    assert io_failure.state.phase is AppPhase.FAILED
    assert invalid_failure.state.phase is AppPhase.FAILED


def test_save_settings_success_validation_and_io_failure() -> None:
    """Save maps validation and persistence outcomes without losing state."""
    success = make_controller()
    invalid = make_controller()
    io_failure = make_controller(repository=ConfigRepositoryStub(save_error=OSError()))

    assert asyncio.run(success.save_settings(valid_values())) is True
    assert success.state.phase is AppPhase.READY
    assert success.state.message == "Configuration saved."
    assert asyncio.run(invalid.save_settings({})) is False
    assert invalid.state.field_errors["game_executable"] == "This field is required."
    assert asyncio.run(io_failure.save_settings(valid_values())) is False
    assert io_failure.state.message == "Configuration could not be saved."


def test_job_outcomes_and_runner_failure_map_to_terminal_state() -> None:
    """All runner results and an I/O failure produce stable presentation states."""

    async def scenario() -> None:
        for outcome, expected in (
            (JobOutcome.SUCCEEDED, AppPhase.SUCCEEDED),
            (JobOutcome.FAILED, AppPhase.FAILED),
            (JobOutcome.TIMED_OUT, AppPhase.FAILED),
            (JobOutcome.CANCELED, AppPhase.CANCELED),
        ):
            controller = make_controller(executor=ExecutorStub(outcome))
            assert controller.start_job(JobKind.VALIDATE_ENVIRONMENT) is True
            assert controller.start_job(JobKind.RUN_SMOKE_TEST) is False
            assert await controller.save_settings(valid_values()) is False
            await controller.wait_for_idle()
            assert controller.state.phase is expected
            assert controller.state.latest_result is not None

        failure = make_controller(executor=ExecutorStub(error=OSError()))
        assert failure.start_job(JobKind.RUN_SMOKE_TEST) is True
        await failure.wait_for_idle()
        assert failure.state.message == "The runner could not complete the job."

    asyncio.run(scenario())


def test_cancel_and_close_own_task_lifecycle() -> None:
    """Cancellation awaits cleanup and close is idempotent."""

    async def scenario() -> None:
        gate = asyncio.Event()
        controller = make_controller(executor=ExecutorStub(gate=gate))
        assert await controller.cancel_active() is False
        assert controller.start_job(JobKind.RUN_SMOKE_TEST) is True
        await asyncio.sleep(0)
        assert await controller.cancel_active() is True
        assert controller.state.phase is AppPhase.CANCELED
        await controller.wait_for_idle()
        await controller.close()
        await controller.close()
        await controller.initialize()
        assert controller.start_job(JobKind.VALIDATE_ENVIRONMENT) is False
        assert await controller.save_settings(valid_values()) is False
        assert controller.state.phase.value == "closed"

    asyncio.run(scenario())


def test_close_rejects_stale_completion() -> None:
    """A cancellation-resistant adapter cannot overwrite closed state."""

    async def scenario() -> None:
        gate = asyncio.Event()
        controller = make_controller(
            executor=ExecutorStub(gate=gate, ignore_cancellation=True),
        )
        assert controller.start_job(JobKind.RUN_SMOKE_TEST) is True
        await asyncio.sleep(0)
        await controller.close()
        assert controller.state.phase is AppPhase.CLOSED

    asyncio.run(scenario())


def test_close_rejects_stale_failure() -> None:
    """A stale adapter failure cannot replace the closed state."""

    async def scenario() -> None:
        gate = asyncio.Event()
        controller = make_controller(
            executor=ExecutorStub(
                error=OSError(),
                gate=gate,
                ignore_cancellation=True,
            ),
        )
        assert controller.start_job(JobKind.RUN_SMOKE_TEST) is True
        await asyncio.sleep(0)
        await controller.close()
        assert controller.state.phase.value == "closed"

    asyncio.run(scenario())


def test_phase_for_outcome_is_exhaustive() -> None:
    """Result variants are mapped without color- or string-based inference."""
    assert phase_for_outcome(JobOutcome.SUCCEEDED) is AppPhase.SUCCEEDED
    assert phase_for_outcome(JobOutcome.CANCELED) is AppPhase.CANCELED
    assert phase_for_outcome(JobOutcome.FAILED) is AppPhase.FAILED
