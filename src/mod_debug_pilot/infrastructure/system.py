"""System identity and time adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from mod_debug_pilot.application.ports import RequestFactory
from mod_debug_pilot.domain import JobKind, JobRequest, PilotConfig


class SystemRequestFactory(RequestFactory):
    """Create collision-resistant, UTC-stamped requests."""

    def create(self, *, kind: JobKind, config: PilotConfig) -> JobRequest:
        """Create one request using current UTC time and a random identifier."""
        now = datetime.now(UTC)
        job_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
        return JobRequest(
            job_id=job_id,
            kind=kind,
            created_at=now.isoformat(),
            config=config,
        )
