"""Cryptographic identity, pairing, freshness, and replay tests."""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mod_debug_pilot.infrastructure.security import (
    AuthenticationError,
    AuthorizationStore,
    IdentityError,
    PairingBroker,
    canonical_request,
    create_agent_identity,
    create_controller_identity,
    create_ephemeral_controller_identity,
    load_agent_identity,
    load_controller_identity,
    server_ssl_context,
    signed_headers,
)

_PASSPHRASE = "correct horse battery staple"  # noqa: S105 - test fixture
_SHORT_PASSPHRASE = "short"  # noqa: S105 - invalid test fixture
_WRONG_PASSPHRASE = "wrong-passphrase"  # noqa: S105 - invalid test fixture
_INCORRECT_PASSPHRASE = "incorrect password"  # noqa: S105 - invalid test fixture
_WRONG_POLL_TOKEN = "wrong"  # noqa: S105 - invalid test fixture
_SHA256_OCTETS = 32
_PAIRING_CODE_DIGITS = 8


def test_agent_identity_create_load_and_tls_context(tmp_path: Path) -> None:
    """The encrypted key matches its certificate and fingerprint on reload."""
    identity = create_agent_identity(tmp_path, passphrase=_PASSPHRASE, common_name="Agent")
    loaded = load_agent_identity(tmp_path, passphrase=_PASSPHRASE)
    context = server_ssl_context(loaded, passphrase=_PASSPHRASE)

    assert loaded == identity
    assert len(identity.fingerprint.split(":")) == _SHA256_OCTETS
    assert context.minimum_version is not None
    with pytest.raises(IdentityError):
        create_agent_identity(tmp_path, passphrase=_PASSPHRASE, common_name="Agent")


def test_agent_identity_rejects_passphrase_and_mismatch(tmp_path: Path) -> None:
    """Short, incorrect, missing, and mismatched key material fails generically."""
    with pytest.raises(IdentityError):
        create_agent_identity(
            tmp_path / "short",
            passphrase=_SHORT_PASSPHRASE,
            common_name="Agent",
        )
    first = create_agent_identity(tmp_path / "one", passphrase=_PASSPHRASE, common_name="One")
    second = create_agent_identity(tmp_path / "two", passphrase=_PASSPHRASE, common_name="Two")
    with pytest.raises(IdentityError):
        load_agent_identity(tmp_path / "one", passphrase=_WRONG_PASSPHRASE)
    shutil.copy2(second.certificate_path, first.certificate_path)
    with pytest.raises(IdentityError):
        load_agent_identity(tmp_path / "one", passphrase=_PASSPHRASE)
    with pytest.raises(IdentityError):
        load_agent_identity(tmp_path / "missing", passphrase=_PASSPHRASE)


def test_controller_identity_create_load_and_metadata(tmp_path: Path) -> None:
    """Controller IDs derive from the exact encrypted Ed25519 public key."""
    path = tmp_path / "controller.pem"
    created = create_controller_identity(
        path,
        passphrase=_PASSPHRASE,
        controller_name="Controller",
    )
    loaded = load_controller_identity(path, passphrase=_PASSPHRASE)

    assert loaded.controller_id == created.controller_id
    assert loaded.public_key_b64 == created.public_key_b64
    with pytest.raises(IdentityError):
        create_controller_identity(path, passphrase=_PASSPHRASE, controller_name="Again")


def test_controller_identity_rejections(tmp_path: Path) -> None:
    """Identity creation and loading reject invalid secrets and metadata."""
    with pytest.raises(IdentityError):
        create_controller_identity(
            tmp_path / "short.pem",
            passphrase=_SHORT_PASSPHRASE,
            controller_name="Controller",
        )
    with pytest.raises(IdentityError):
        create_controller_identity(
            tmp_path / "empty.pem",
            passphrase=_PASSPHRASE,
            controller_name="",
        )
    with pytest.raises(IdentityError):
        create_controller_identity(
            tmp_path / "long.pem",
            passphrase=_PASSPHRASE,
            controller_name="x" * 65,
        )
    path = tmp_path / "controller.pem"
    create_controller_identity(path, passphrase=_PASSPHRASE, controller_name="Controller")
    with pytest.raises(IdentityError):
        load_controller_identity(path, passphrase=_INCORRECT_PASSPHRASE)
    path.with_suffix(".json").write_text("{}", encoding="utf-8")
    with pytest.raises(IdentityError):
        load_controller_identity(path, passphrase=_PASSPHRASE)
    path.write_text("not a key", encoding="utf-8")
    with pytest.raises(IdentityError):
        load_controller_identity(path, passphrase=_PASSPHRASE)
    rsa_path = tmp_path / "rsa.pem"
    rsa_path.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(_PASSPHRASE.encode()),
        )
    )
    rsa_path.with_suffix(".json").write_text("{}", encoding="utf-8")
    with pytest.raises(IdentityError, match="identity is invalid"):
        load_controller_identity(rsa_path, passphrase=_PASSPHRASE)


def test_ephemeral_controller_validation() -> None:
    """Web sessions receive memory-only unique signing identities."""
    first = create_ephemeral_controller_identity(" Browser ")
    second = create_ephemeral_controller_identity("Browser")
    assert first.name == "Browser"
    assert first.controller_id != second.controller_id
    for name in ("", "x" * 65):
        with pytest.raises(IdentityError):
            create_ephemeral_controller_identity(name)


def test_signed_request_verification_and_replay(tmp_path: Path) -> None:
    """An approved signature verifies once and binds method, path, and body."""
    identity = create_ephemeral_controller_identity("Controller")
    store = AuthorizationStore(tmp_path / "approved.json")
    store.approve(
        controller_id=identity.controller_id,
        name=identity.name,
        public_key_b64=identity.public_key_b64,
    )
    headers = signed_headers(
        identity,
        method="POST",
        path="/v1/test",
        body=b"payload",
        now=1000,
        nonce="nonce",
    )
    assert canonical_request(
        method="post",
        path="/v1/test",
        body=b"payload",
        timestamp="1000",
        nonce="nonce",
    ).startswith(b"MDP1\nPOST")

    name = store.verify(
        controller_id=headers["X-MDP-Controller"],
        method="POST",
        path="/v1/test",
        body=b"payload",
        timestamp=headers["X-MDP-Timestamp"],
        nonce=headers["X-MDP-Nonce"],
        signature_b64=headers["X-MDP-Signature"],
        now=1000,
    )
    assert name == "Controller"
    with pytest.raises(AuthenticationError, match="already used"):
        store.verify(
            controller_id=identity.controller_id,
            method="POST",
            path="/v1/test",
            body=b"payload",
            timestamp="1000",
            nonce="nonce",
            signature_b64=headers["X-MDP-Signature"],
            now=1000,
        )


def test_signature_failure_modes(tmp_path: Path) -> None:
    """Stale, malformed, unknown, tampered, and invalid signatures fail closed."""
    identity = create_ephemeral_controller_identity("Controller")
    store = AuthorizationStore(tmp_path / "approved.json")
    store.approve(
        controller_id=identity.controller_id,
        name=identity.name,
        public_key_b64=identity.public_key_b64,
    )
    headers = signed_headers(
        identity,
        method="GET",
        path="/x",
        body=b"",
        now=1000,
        nonce="n1",
    )
    common = {
        "controller_id": identity.controller_id,
        "method": "GET",
        "path": "/x",
        "body": b"",
        "timestamp": "1000",
        "nonce": "n2",
        "signature_b64": headers["X-MDP-Signature"],
        "now": 1000,
    }
    with pytest.raises(AuthenticationError, match="timestamp"):
        store.verify(**{**common, "timestamp": "bad"})  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError, match="stale"):
        store.verify(**{**common, "now": 2000})  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError, match="not approved"):
        store.verify(**{**common, "controller_id": "unknown"})  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError, match="signature"):
        store.verify(**common)  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError, match="signature"):
        store.verify(**{**common, "signature_b64": "%%%"})  # type: ignore[arg-type]


def test_authorization_store_reload_and_corruption(tmp_path: Path) -> None:
    """Approved keys survive reload while every malformed store shape is rejected."""
    identity = create_ephemeral_controller_identity("Controller")
    path = tmp_path / "approved.json"
    store = AuthorizationStore(path)
    store.approve(
        controller_id=identity.controller_id,
        name=identity.name,
        public_key_b64=identity.public_key_b64,
    )
    AuthorizationStore(path)

    invalid_payloads = [
        "not json",
        json.dumps({"controllers": []}),
        json.dumps({"controllers": {"id": []}}),
        json.dumps({"controllers": {"id": {}}}),
        json.dumps({"controllers": {"id": {"name": 1, "public_key": 2}}}),
        json.dumps({"controllers": {"id": {"name": "n", "public_key": "bad"}}}),
    ]
    for index, payload in enumerate(invalid_payloads):
        corrupt = tmp_path / f"corrupt-{index}.json"
        corrupt.write_text(payload, encoding="utf-8")
        with pytest.raises(IdentityError):
            AuthorizationStore(corrupt)
    with pytest.raises(AuthenticationError):
        store.approve(controller_id="id", name="n", public_key_b64="bad")


def test_pairing_approval_rejection_and_expiry(tmp_path: Path) -> None:
    """The one-time code creates one pending request requiring a local decision."""
    store = AuthorizationStore(tmp_path / "approved.json")
    broker = PairingBroker(store)
    identity = create_ephemeral_controller_identity("Browser")
    code = broker.open(now=100)
    assert len(code) == _PAIRING_CODE_DIGITS
    pending = broker.request(
        code=code,
        controller_id=identity.controller_id,
        controller_name=identity.name,
        public_key_b64=identity.public_key_b64,
        now=101,
    )
    assert broker.pending() == (pending,)
    assert broker.status(pending.request_id, poll_token=pending.poll_token) is None
    broker.decide(pending.request_id, approve=True)
    assert broker.pending() == ()
    assert broker.status(pending.request_id, poll_token=pending.poll_token) is True
    with pytest.raises(AuthenticationError, match="already decided"):
        broker.decide(pending.request_id, approve=False)

    code = broker.open(now=200)
    rejected = broker.request(
        code=code,
        controller_id=identity.controller_id,
        controller_name=identity.name,
        public_key_b64=identity.public_key_b64,
        now=201,
    )
    broker.decide(rejected.request_id, approve=False)
    assert broker.status(rejected.request_id, poll_token=rejected.poll_token) is False

    expired = broker.open(now=300)
    with pytest.raises(AuthenticationError, match="expired"):
        broker.request(
            code=expired,
            controller_id=identity.controller_id,
            controller_name=identity.name,
            public_key_b64=identity.public_key_b64,
            now=901,
        )


def test_pairing_rejects_wrong_code_key_id_and_poll_token(tmp_path: Path) -> None:
    """Pairing secrets and public-key-derived IDs cannot be substituted."""
    broker = PairingBroker(AuthorizationStore(tmp_path / "approved.json"))
    identity = create_ephemeral_controller_identity("Browser")
    code = broker.open(now=1)
    with pytest.raises(AuthenticationError, match="expired"):
        broker.request(
            code="wrong",
            controller_id=identity.controller_id,
            controller_name=identity.name,
            public_key_b64=identity.public_key_b64,
            now=2,
        )
    code = broker.open(now=3)
    with pytest.raises(AuthenticationError, match="does not match"):
        broker.request(
            code=code,
            controller_id="wrong",
            controller_name=identity.name,
            public_key_b64=identity.public_key_b64,
            now=4,
        )
    code = broker.open(now=5)
    with pytest.raises(AuthenticationError, match="public key"):
        broker.request(
            code=code,
            controller_id="wrong",
            controller_name=identity.name,
            public_key_b64=base64.b64encode(b"short").decode(),
            now=6,
        )
    code = broker.open(now=7)
    pending = broker.request(
        code=code,
        controller_id=identity.controller_id,
        controller_name=identity.name,
        public_key_b64=identity.public_key_b64,
        now=8,
    )
    with pytest.raises(AuthenticationError, match="not found"):
        broker.status(pending.request_id, poll_token=_WRONG_POLL_TOKEN)


def test_pairing_bounds_guesses_and_keeps_web_approval_ephemeral(tmp_path: Path) -> None:
    """Five wrong guesses close pairing and Web approval writes no durable key."""
    authorization_path = tmp_path / "approved.json"
    broker = PairingBroker(AuthorizationStore(authorization_path))
    identity = create_ephemeral_controller_identity("Browser")
    code = broker.open(now=1)
    for attempt in range(5):
        with pytest.raises(AuthenticationError, match="expired"):
            broker.request(
                code=f"wrong-{attempt}",
                controller_id=identity.controller_id,
                controller_name=identity.name,
                public_key_b64=identity.public_key_b64,
                now=2,
            )
    with pytest.raises(AuthenticationError, match="expired"):
        broker.request(
            code=code,
            controller_id=identity.controller_id,
            controller_name=identity.name,
            public_key_b64=identity.public_key_b64,
            now=3,
        )

    code = broker.open(now=4)
    pending = broker.request(
        code=code,
        controller_id=identity.controller_id,
        controller_name=identity.name,
        public_key_b64=identity.public_key_b64,
        persist_authorization=False,
        now=5,
    )
    broker.decide(pending.request_id, approve=True)
    assert broker.status(pending.request_id, poll_token=pending.poll_token) is True
    assert not authorization_path.exists()
