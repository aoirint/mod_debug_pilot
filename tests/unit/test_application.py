"""Tests for settings and job use cases."""

from __future__ import annotations

import asyncio

from mod_debug_pilot.application.services import JobService, SettingsService
from mod_debug_pilot.domain import JobKind, JobOutcome, JobRequest, JobResult, PilotConfig
from tests.unit.test_domain import valid_values


class MemoryRepository:
    """In-memory settings adapter for use-case tests."""

    def __init__(self, value: PilotConfig | None) -> None:
        """Store an optional snapshot."""
        self.value = value
        self.saved: PilotConfig | None = None

    async def load(self) -> PilotConfig | None:
        """Return the stored snapshot."""
        return self.value

    async def save(self, config: PilotConfig) -> None:
        """Record the saved snapshot."""
        self.saved = config


class FixedRequestFactory:
    """Deterministic request factory."""

    def create(self, kind: JobKind, *, config: PilotConfig) -> JobRequest:
        """Create a fixed request."""
        return JobRequest(job_id="fixed", kind=kind, created_at="now", config=config)


class RecordingExecutor:
    """Executor that records the handed-off request."""

    def __init__(self) -> None:
        """Create an empty recorder."""
        self.request: JobRequest | None = None

    async def execute(self, request: JobRequest) -> JobResult:
        """Record and complete the request."""
        self.request = request
        return JobResult(
            job_id=request.job_id,
            outcome=JobOutcome.SUCCEEDED,
            message="ok",
            artifact_dir="artifacts",
            started_at="start",
            finished_at="finish",
        )


def test_settings_service_loads_defaults_and_saves_validated_values() -> None:
    """First use receives defaults and form submissions are persisted."""
    repository = MemoryRepository(None)
    service = SettingsService(repository)

    assert asyncio.run(service.load()) == PilotConfig.defaults()
    saved = asyncio.run(service.save(valid_values()))

    assert repository.saved == saved


def test_settings_service_returns_saved_configuration() -> None:
    """A saved snapshot takes precedence over first-run defaults."""
    config = PilotConfig.from_mapping(valid_values())

    assert asyncio.run(SettingsService(MemoryRepository(config)).load()) == config


def test_job_service_creates_and_executes_request() -> None:
    """The use case snapshots the selected allow-listed job."""
    executor = RecordingExecutor()
    service = JobService(executor, request_factory=FixedRequestFactory())
    config = PilotConfig.from_mapping(valid_values())

    result = asyncio.run(service.run(JobKind.VALIDATE_ENVIRONMENT, config=config))

    assert result.succeeded is True
    assert executor.request is not None
    assert executor.request.kind is JobKind.VALIDATE_ENVIRONMENT
