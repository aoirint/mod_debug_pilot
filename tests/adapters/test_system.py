"""Tests for request identity generation."""

from __future__ import annotations

from datetime import datetime

from mod_debug_pilot.domain import JobKind, PilotConfig
from mod_debug_pilot.infrastructure.system import SystemRequestFactory
from tests.unit.test_domain import valid_values


def test_system_request_factory_uses_utc_and_safe_identifier() -> None:
    """Generated identifiers are artifact-directory safe and UTC stamped."""
    request = SystemRequestFactory().create(
        JobKind.RUN_SMOKE_TEST,
        config=PilotConfig.from_mapping(valid_values()),
    )

    assert request.job_id.replace("-", "").isalnum()
    timestamp, suffix = request.job_id.split("-")
    assert timestamp.endswith("Z")
    assert suffix
    offset = datetime.fromisoformat(request.created_at).utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0
