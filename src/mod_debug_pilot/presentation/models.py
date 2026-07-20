"""Immutable state rendered by the Flet adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mod_debug_pilot.domain.models import JobKind, JobResult, PilotConfig


class AppPhase(StrEnum):
    """Mutually exclusive application presentation phases."""

    LOADING = "loading"
    READY = "ready"
    SAVING = "saving"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True, kw_only=True)
class AppState:
    """One coherent UI snapshot."""

    phase: AppPhase
    config: PilotConfig
    message: str
    field_errors: dict[str, str]
    active_job: JobKind | None = None
    latest_result: JobResult | None = None

    @classmethod
    def initial(cls) -> AppState:
        """Return the state displayed before loading persistence."""
        return cls(
            phase=AppPhase.LOADING,
            config=PilotConfig.defaults(),
            message="Loading configuration…",
            field_errors={},
        )
