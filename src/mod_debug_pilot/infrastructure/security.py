"""TLS identity, pairing, and signed-request primitives."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import time
from typing import Final

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

from mod_debug_pilot.infrastructure.settings import write_json_atomic

_CLOCK_SKEW_SECONDS: Final = 90
_NONCE_LIFETIME_SECONDS: Final = 300
_PAIRING_ATTEMPTS: Final = 5


class IdentityError(ValueError):
    """Report invalid passphrases or identity files without leaking details."""


class AuthenticationError(ValueError):
    """Reject an unknown signer, stale request, bad signature, or replay."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentIdentity:
    """Paths and public fingerprint for one TLS server identity."""

    certificate_path: Path
    private_key_path: Path
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ControllerIdentity:
    """Encrypted signing identity used by a controller."""

    controller_id: str
    name: str
    private_key: ed25519.Ed25519PrivateKey

    @property
    def public_key_b64(self) -> str:
        """Return the raw public key in transport encoding."""
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw).decode("ascii")


def create_ephemeral_controller_identity(name: str) -> ControllerIdentity:
    """Create a memory-only signing identity for one approved web session."""
    normalized = name.strip()
    if not normalized or len(normalized) > 64:
        raise IdentityError("Controller name is invalid.")
    key = ed25519.Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return ControllerIdentity(
        controller_id=hashlib.sha256(raw).hexdigest()[:32],
        name=normalized,
        private_key=key,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PairingRequest:
    """One controller awaiting local agent approval."""

    request_id: str
    controller_id: str
    controller_name: str
    public_key_b64: str
    poll_token: str
    created_at: float
    persist_authorization: bool
    approved: bool | None = None


def create_agent_identity(directory: Path, *, passphrase: str, common_name: str) -> AgentIdentity:
    """Create an encrypted RSA key and self-signed TLS certificate."""
    if len(passphrase) < 12:
        raise IdentityError("Use an agent passphrase of at least 12 characters.")
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / "agent-key.pem"
    cert_path = directory / "agent-cert.pem"
    if key_path.exists() or cert_path.exists():
        raise IdentityError("Agent identity already exists.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name[:64])])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return AgentIdentity(
        certificate_path=cert_path,
        private_key_path=key_path,
        fingerprint=_certificate_fingerprint(certificate),
    )


def load_agent_identity(directory: Path, *, passphrase: str) -> AgentIdentity:
    """Validate an existing encrypted TLS identity."""
    key_path = directory / "agent-key.pem"
    cert_path = directory / "agent-cert.pem"
    try:
        key = serialization.load_pem_private_key(
            key_path.read_bytes(),
            password=passphrase.encode("utf-8"),
        )
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, TypeError, ValueError) as error:
        raise IdentityError("Agent identity or passphrase is invalid.") from error
    if not isinstance(key, rsa.RSAPrivateKey) or key.public_key().public_numbers() != (
        certificate.public_key().public_numbers()  # type: ignore[union-attr]
    ):
        raise IdentityError("Agent certificate does not match its private key.")
    return AgentIdentity(
        certificate_path=cert_path,
        private_key_path=key_path,
        fingerprint=_certificate_fingerprint(certificate),
    )


def server_ssl_context(identity: AgentIdentity, *, passphrase: str) -> ssl.SSLContext:
    """Create a TLS 1.2+ server context from an encrypted identity."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        certfile=identity.certificate_path,
        keyfile=identity.private_key_path,
        password=passphrase,
    )
    return context


def create_controller_identity(
    path: Path,
    *,
    passphrase: str,
    controller_name: str,
) -> ControllerIdentity:
    """Create one passphrase-encrypted Ed25519 signing identity."""
    if len(passphrase) < 12:
        raise IdentityError("Use a controller passphrase of at least 12 characters.")
    if not controller_name.strip() or len(controller_name) > 64:
        raise IdentityError("Controller name is invalid.")
    if path.exists():
        raise IdentityError("Controller identity already exists.")
    key = ed25519.Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
        )
    )
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    controller_id = hashlib.sha256(public_raw).hexdigest()[:32]
    metadata = {"schema_version": 1, "controller_id": controller_id, "name": controller_name}
    write_json_atomic(path.with_suffix(".json"), payload=metadata)
    return ControllerIdentity(controller_id=controller_id, name=controller_name, private_key=key)


def load_controller_identity(path: Path, *, passphrase: str) -> ControllerIdentity:
    """Load and cross-check one encrypted controller identity."""
    try:
        key = serialization.load_pem_private_key(
            path.read_bytes(),
            password=passphrase.encode("utf-8"),
        )
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IdentityError("Controller identity or passphrase is invalid.") from error
    if not isinstance(key, ed25519.Ed25519PrivateKey) or not isinstance(metadata, dict):
        raise IdentityError("Controller identity is invalid.")
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    expected_id = hashlib.sha256(public_raw).hexdigest()[:32]
    controller_id = metadata.get("controller_id")
    name = metadata.get("name")
    if controller_id != expected_id or not isinstance(name, str):
        raise IdentityError("Controller identity metadata does not match.")
    return ControllerIdentity(controller_id=expected_id, name=name, private_key=key)


def canonical_request(*, method: str, path: str, body: bytes, timestamp: str, nonce: str) -> bytes:
    """Build the versioned byte string signed for every protected request."""
    body_digest = hashlib.sha256(body).hexdigest()
    return f"MDP1\n{method.upper()}\n{path}\n{body_digest}\n{timestamp}\n{nonce}".encode()


def signed_headers(
    identity: ControllerIdentity,
    *,
    method: str,
    path: str,
    body: bytes,
    now: float | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Sign one request with a fresh nonce and UTC timestamp."""
    timestamp = str(int(time() if now is None else now))
    resolved_nonce = nonce or secrets.token_urlsafe(24)
    message = canonical_request(
        method=method,
        path=path,
        body=body,
        timestamp=timestamp,
        nonce=resolved_nonce,
    )
    signature = identity.private_key.sign(message)
    return {
        "X-MDP-Controller": identity.controller_id,
        "X-MDP-Timestamp": timestamp,
        "X-MDP-Nonce": resolved_nonce,
        "X-MDP-Signature": base64.b64encode(signature).decode("ascii"),
    }


class AuthorizationStore:
    """Persist the exact controller public keys approved by the agent operator."""

    def __init__(self, path: Path) -> None:
        """Create a store at a private application-data path."""
        self._path = path
        self._keys: dict[str, tuple[str, str]] = {}
        self._nonces: dict[str, float] = {}
        self._load()

    def approve(self, *, controller_id: str, name: str, public_key_b64: str) -> None:
        """Persist one approved signing key."""
        _decode_public_key(public_key_b64)
        self._keys[controller_id] = (name, public_key_b64)
        payload = {
            "schema_version": 1,
            "controllers": {
                key: {"name": item[0], "public_key": item[1]}
                for key, item in sorted(self._keys.items())
            },
        }
        write_json_atomic(self._path, payload=payload)

    def verify(
        self,
        *,
        controller_id: str,
        method: str,
        path: str,
        body: bytes,
        timestamp: str,
        nonce: str,
        signature_b64: str,
        now: float | None = None,
    ) -> str:
        """Verify authorization, freshness, signature, and one-use nonce."""
        resolved_now = time() if now is None else now
        try:
            request_time = int(timestamp)
        except ValueError as error:
            raise AuthenticationError("Invalid request timestamp.") from error
        if abs(resolved_now - request_time) > _CLOCK_SKEW_SECONDS:
            raise AuthenticationError("Request timestamp is stale.")
        self._nonces = {
            key: expiry for key, expiry in self._nonces.items() if expiry > resolved_now
        }
        nonce_key = f"{controller_id}:{nonce}"
        if nonce_key in self._nonces:
            raise AuthenticationError("Request nonce was already used.")
        approved = self._keys.get(controller_id)
        if approved is None:
            raise AuthenticationError("Controller is not approved.")
        public_key = _decode_public_key(approved[1])
        try:
            signature = base64.b64decode(signature_b64, validate=True)
            public_key.verify(
                signature,
                canonical_request(
                    method=method,
                    path=path,
                    body=body,
                    timestamp=timestamp,
                    nonce=nonce,
                ),
            )
        except (InvalidSignature, ValueError, TypeError) as error:
            raise AuthenticationError("Request signature is invalid.") from error
        self._nonces[nonce_key] = resolved_now + _NONCE_LIFETIME_SECONDS
        return approved[0]

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            controllers = payload["controllers"]
            if not isinstance(controllers, dict):
                raise TypeError
            for controller_id, item in controllers.items():
                if not isinstance(controller_id, str) or not isinstance(item, dict):
                    raise TypeError
                name = item["name"]
                public_key = item["public_key"]
                if not isinstance(name, str) or not isinstance(public_key, str):
                    raise TypeError
                _decode_public_key(public_key)
                self._keys[controller_id] = (name, public_key)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise IdentityError("Approved-controller store is invalid.") from error


class PairingBroker:
    """Keep a short-lived one-time code and locally approved pending requests."""

    def __init__(self, authorizations: AuthorizationStore) -> None:
        """Create an initially closed broker."""
        self._authorizations = authorizations
        self._code: str | None = None
        self._expires_at = 0.0
        self._attempts_remaining = 0
        self._requests: dict[str, PairingRequest] = {}

    def open(self, *, now: float | None = None) -> str:
        """Open a ten-minute pairing window and return an eight-digit code."""
        resolved_now = time() if now is None else now
        self._code = f"{secrets.randbelow(100_000_000):08d}"
        self._expires_at = resolved_now + 600
        self._attempts_remaining = _PAIRING_ATTEMPTS
        return self._code

    def request(
        self,
        *,
        code: str,
        controller_id: str,
        controller_name: str,
        public_key_b64: str,
        persist_authorization: bool = True,
        now: float | None = None,
    ) -> PairingRequest:
        """Create a pending request after validating the one-time code."""
        resolved_now = time() if now is None else now
        if self._code is None or resolved_now > self._expires_at:
            raise AuthenticationError("Pairing code is invalid or expired.")
        if not secrets.compare_digest(code, self._code):
            self._attempts_remaining -= 1
            if self._attempts_remaining <= 0:
                self._code = None
            raise AuthenticationError("Pairing code is invalid or expired.")
        # A correctly presented code is one-use even when the remaining payload is malformed.
        self._code = None
        self._attempts_remaining = 0
        _decode_public_key(public_key_b64)
        expected_id = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
        if controller_id != expected_id:
            raise AuthenticationError("Controller identity does not match its key.")
        request = PairingRequest(
            request_id=secrets.token_urlsafe(18),
            controller_id=controller_id,
            controller_name=controller_name[:64],
            public_key_b64=public_key_b64,
            poll_token=secrets.token_urlsafe(32),
            created_at=resolved_now,
            persist_authorization=persist_authorization,
        )
        self._requests[request.request_id] = request
        return request

    def pending(self) -> tuple[PairingRequest, ...]:
        """Return requests that still need a local decision."""
        return tuple(item for item in self._requests.values() if item.approved is None)

    def decide(self, request_id: str, *, approve: bool) -> None:
        """Apply the local operator's explicit decision."""
        request = self._requests[request_id]
        if request.approved is not None:
            raise AuthenticationError("Pairing request was already decided.")
        decided = PairingRequest(
            request_id=request.request_id,
            controller_id=request.controller_id,
            controller_name=request.controller_name,
            public_key_b64=request.public_key_b64,
            poll_token=request.poll_token,
            created_at=request.created_at,
            persist_authorization=request.persist_authorization,
            approved=approve,
        )
        self._requests[request_id] = decided
        if approve and request.persist_authorization:
            self._authorizations.approve(
                controller_id=request.controller_id,
                name=request.controller_name,
                public_key_b64=request.public_key_b64,
            )

    def status(self, request_id: str, *, poll_token: str) -> bool | None:
        """Return pending/approved/rejected to the matching polling secret."""
        request = self._requests.get(request_id)
        if request is None or not secrets.compare_digest(request.poll_token, poll_token):
            raise AuthenticationError("Pairing request was not found.")
        return request.approved


def _certificate_fingerprint(certificate: x509.Certificate) -> str:
    digest = certificate.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def _decode_public_key(value: str) -> ed25519.Ed25519PublicKey:
    try:
        raw = base64.b64decode(value, validate=True)
        return ed25519.Ed25519PublicKey.from_public_bytes(raw)
    except (TypeError, ValueError) as error:
        raise AuthenticationError("Controller public key is invalid.") from error
