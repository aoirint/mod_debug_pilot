"""Tests for short-lived local browser approval."""

from __future__ import annotations

import pytest

from mod_debug_pilot.application import PairingBroker
from mod_debug_pilot.domain import PairingError


def test_pairing_approval_and_rejection() -> None:
    """A valid one-use code creates a request decided only by the Agent."""
    broker = PairingBroker()
    code = broker.open(now=100)
    request = broker.request(code=code, controller_name=" Browser ", now=101)

    assert request.controller_name == "Browser"
    assert broker.pending() == (request,)
    assert broker.status(request_id=request.request_id, poll_token=request.poll_token) is None
    broker.decide(request_id=request.request_id, approve=True)
    assert broker.pending() == ()
    assert broker.status(request_id=request.request_id, poll_token=request.poll_token) is True
    with pytest.raises(PairingError, match="already"):
        broker.decide(request_id=request.request_id, approve=False)

    rejected_code = broker.open()
    rejected = broker.request(code=rejected_code, controller_name="Rejected")
    broker.decide(request_id=rejected.request_id, approve=False)
    assert broker.status(request_id=rejected.request_id, poll_token=rejected.poll_token) is False


def test_pairing_rejections_and_attempt_limit() -> None:
    """Closed, expired, guessed, empty, and unknown requests fail closed."""
    broker = PairingBroker()
    with pytest.raises(PairingError, match="invalid"):
        broker.request(code="00000000", controller_name="Browser", now=1)

    expired = broker.open(now=10)
    with pytest.raises(PairingError, match="expired"):
        broker.request(code=expired, controller_name="Browser", now=611)

    actual = broker.open(now=20)
    for _attempt in range(5):
        with pytest.raises(PairingError, match="invalid"):
            broker.request(code="wrong", controller_name="Browser", now=21)
    with pytest.raises(PairingError, match="invalid"):
        broker.request(code=actual, controller_name="Browser", now=21)

    empty_name = broker.open(now=30)
    with pytest.raises(PairingError, match="name"):
        broker.request(code=empty_name, controller_name=" ", now=31)
    with pytest.raises(PairingError, match="invalid"):
        broker.request(code=empty_name, controller_name="Browser", now=31)

    with pytest.raises(PairingError, match="not found"):
        broker.decide(request_id="missing", approve=True)
    with pytest.raises(PairingError, match="not found"):
        broker.status(request_id="missing", poll_token="bad")
