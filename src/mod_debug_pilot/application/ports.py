"""Typed boundaries for external effects."""

from __future__ import annotations

from typing import Protocol

from mod_debug_pilot.domain.models import JobKind, JobRequest, JobResult, PilotConfig


class ConfigRepository(Protocol):
    """Persist non-secret application configuration."""

    async def load(self) -> PilotConfig | None:
        """Load the configuration, returning none on first use."""
        ...

    async def save(self, *, config: PilotConfig) -> None:
        """Persist one validated snapshot."""
        ...


class JobExecutor(Protocol):
    """Execute one allow-listed request."""

    async def execute(self, *, request: JobRequest) -> JobResult:
        """Run the request and return its terminal result."""
        ...


class RequestFactory(Protocol):
    """Create uniquely identified requests using system time."""

    def create(self, *, kind: JobKind, config: PilotConfig) -> JobRequest:
        """Create one immutable request."""
        ...
