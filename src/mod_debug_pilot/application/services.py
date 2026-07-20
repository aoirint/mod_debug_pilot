"""Small use cases coordinating domain values and external ports."""

from __future__ import annotations

from mod_debug_pilot.application.ports import ConfigRepository, JobExecutor, RequestFactory
from mod_debug_pilot.domain.models import JobKind, JobResult, PilotConfig


class SettingsService:
    """Load and save validated public configuration."""

    def __init__(self, *, repository: ConfigRepository) -> None:
        """Create the service around one repository."""
        self._repository = repository

    async def load(self) -> PilotConfig:
        """Load saved values or return first-run defaults."""
        return await self._repository.load() or PilotConfig.defaults()

    async def save(self, *, values: dict[str, object]) -> PilotConfig:
        """Validate form values before persisting them."""
        config = PilotConfig.from_mapping(values=values)
        await self._repository.save(config=config)
        return config


class JobService:
    """Create and execute one allow-listed job."""

    def __init__(self, *, executor: JobExecutor, request_factory: RequestFactory) -> None:
        """Create the service with explicit effect owners."""
        self._executor = executor
        self._request_factory = request_factory

    async def run(self, *, kind: JobKind, config: PilotConfig) -> JobResult:
        """Create a request snapshot and execute it."""
        request = self._request_factory.create(kind=kind, config=config)
        return await self._executor.execute(request=request)
