"""Short-lived browser pairing values."""

from __future__ import annotations

from dataclasses import dataclass


class PairingError(ValueError):
    """Reject an invalid or unauthorized browser session."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PairingRequest:
    """One browser session awaiting a local operator decision."""

    request_id: str
    controller_id: str
    controller_name: str
    poll_token: str
    created_at: float
    approved: bool | None = None
