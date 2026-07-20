"""Framework-independent domain values."""

from mod_debug_pilot.domain.pairing import PairingError, PairingRequest
from mod_debug_pilot.domain.profiles import ImportedProfile, ProfileError, ThunderstoreMod
from mod_debug_pilot.domain.remote import (
    AgentSettings,
    BundleManifest,
    FileRecord,
    InstanceSnapshot,
    InstanceSpec,
    InstanceStatus,
    RemoteValidationError,
)

__all__ = [
    "AgentSettings",
    "BundleManifest",
    "FileRecord",
    "ImportedProfile",
    "InstanceSnapshot",
    "InstanceSpec",
    "InstanceStatus",
    "PairingError",
    "PairingRequest",
    "ProfileError",
    "RemoteValidationError",
    "ThunderstoreMod",
]
