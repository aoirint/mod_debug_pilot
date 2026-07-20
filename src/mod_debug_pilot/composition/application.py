"""Select concrete adapters for the desktop application."""

from __future__ import annotations

import os
from pathlib import Path

from mod_debug_pilot.application import JobService, SettingsService
from mod_debug_pilot.infrastructure import (
    JsonConfigRepository,
    LocalJobExecutor,
    SystemRequestFactory,
    application_data_dir,
)
from mod_debug_pilot.presentation import AppController


def compose_controller(*, data_dir: Path | None = None) -> AppController:
    """Create one page-session controller and its concrete effect owners."""
    resolved_data_dir = data_dir or application_data_dir(environment=os.environ, home=Path.home())
    settings = SettingsService(
        repository=JsonConfigRepository(path=resolved_data_dir / "settings.json")
    )
    jobs = JobService(
        executor=LocalJobExecutor.system_default(), request_factory=SystemRequestFactory()
    )
    return AppController(settings=settings, jobs=jobs)
