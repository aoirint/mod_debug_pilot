"""Framework-independent domain values."""

from mod_debug_pilot.domain.models import (
    JobKind,
    JobOutcome,
    JobRequest,
    JobResult,
    PilotConfig,
    ValidationError,
)
from mod_debug_pilot.domain.remote import (
    AgentSettings,
    BundleManifest,
    FileRecord,
    InstanceSnapshot,
    InstanceSpec,
    InstanceStatus,
    RemoteValidationError,
    normalize_fingerprint,
)

__all__ = [
    "AgentSettings",
    "BundleManifest",
    "FileRecord",
    "InstanceSnapshot",
    "InstanceSpec",
    "InstanceStatus",
    "JobKind",
    "JobOutcome",
    "JobRequest",
    "JobResult",
    "PilotConfig",
    "RemoteValidationError",
    "ValidationError",
    "normalize_fingerprint",
]
