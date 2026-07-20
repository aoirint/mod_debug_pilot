"""Local-approval policy for short-lived browser sessions."""

from __future__ import annotations

import secrets
from time import time

from mod_debug_pilot.domain import PairingError, PairingRequest

_PAIRING_ATTEMPTS = 5
_PAIRING_LIFETIME_SECONDS = 600


class PairingBroker:
    """Keep one short-lived code and its locally decided requests."""

    def __init__(self) -> None:
        """Create an initially closed broker."""
        self._code: str | None = None
        self._expires_at = 0.0
        self._attempts_remaining = 0
        self._requests: dict[str, PairingRequest] = {}

    def open(self, *, now: float | None = None) -> str:
        """Open a ten-minute window and return an eight-digit code."""
        resolved_now = time() if now is None else now
        self._code = f"{secrets.randbelow(100_000_000):08d}"
        self._expires_at = resolved_now + _PAIRING_LIFETIME_SECONDS
        self._attempts_remaining = _PAIRING_ATTEMPTS
        return self._code

    def request(
        self,
        *,
        code: str,
        controller_name: str,
        now: float | None = None,
    ) -> PairingRequest:
        """Create a pending request after consuming the one-time code."""
        resolved_now = time() if now is None else now
        if self._code is None or resolved_now > self._expires_at:
            raise PairingError("Pairing code is invalid or expired.")
        if not secrets.compare_digest(code, self._code):
            self._attempts_remaining -= 1
            if self._attempts_remaining <= 0:
                self._code = None
            raise PairingError("Pairing code is invalid or expired.")
        self._code = None
        self._attempts_remaining = 0
        name = controller_name.strip()
        if not name:
            raise PairingError("Controller name is required.")
        request = PairingRequest(
            request_id=secrets.token_urlsafe(18),
            controller_id=secrets.token_hex(16),
            controller_name=name[:64],
            poll_token=secrets.token_urlsafe(32),
            created_at=resolved_now,
        )
        self._requests[request.request_id] = request
        return request

    def pending(self) -> tuple[PairingRequest, ...]:
        """Return requests that still need a local decision."""
        return tuple(item for item in self._requests.values() if item.approved is None)

    def decide(self, *, request_id: str, approve: bool) -> None:
        """Apply the local operator's explicit decision."""
        request = self._requests.get(request_id)
        if request is None:
            raise PairingError("Pairing request was not found.")
        if request.approved is not None:
            raise PairingError("Pairing request was already decided.")
        self._requests[request_id] = PairingRequest(
            request_id=request.request_id,
            controller_id=request.controller_id,
            controller_name=request.controller_name,
            poll_token=request.poll_token,
            created_at=request.created_at,
            approved=approve,
        )

    def status(self, *, request_id: str, poll_token: str) -> bool | None:
        """Return the decision only to the matching browser session."""
        request = self._requests.get(request_id)
        if request is None or not secrets.compare_digest(request.poll_token, poll_token):
            raise PairingError("Pairing request was not found.")
        return request.approved
