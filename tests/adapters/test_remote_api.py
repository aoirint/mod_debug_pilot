"""Real pinned-TLS API, pairing, authorization, and bounded response tests."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from mod_debug_pilot.domain import InstanceSnapshot, InstanceSpec, InstanceStatus
from mod_debug_pilot.infrastructure.agent_runtime import AgentRuntimeError, RemoteAgentRuntime
from mod_debug_pilot.infrastructure.profiles import ProfileImportError
from mod_debug_pilot.infrastructure.remote_api import (
    AgentApiClient,
    AgentApiIdentity,
    AgentApiServer,
    RemoteApiError,
    _json_body,
    _required_string,
    _status_for,
    bundle_digest,
)
from mod_debug_pilot.infrastructure.security import (
    AuthenticationError,
    AuthorizationStore,
    PairingBroker,
    create_agent_identity,
    create_ephemeral_controller_identity,
    server_ssl_context,
)

_PASSPHRASE = "remote api integration secret"  # noqa: S105 - test fixture
_HTTP_BAD_REQUEST = 400
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409


class RuntimeStub:
    """In-memory allow-listed runtime used behind the real HTTPS server."""

    def __init__(self, root: Path) -> None:
        """Create empty remote state."""
        self.root = root
        self.recovered = False
        self.instances: dict[str, InstanceSnapshot] = {}
        self.failure: BaseException | None = None

    def maybe_fail(self) -> None:
        """Raise one configured route failure."""
        if self.failure is not None:
            raise self.failure

    async def recover(self) -> None:
        """Record startup recovery."""
        self.recovered = True

    async def install_profile(self, profile_id: str, bundle: bytes) -> str:
        """Return a stable profile name."""
        self.maybe_fail()
        assert profile_id == "profile"
        assert bundle == b"bundle"
        return "Installed"

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Return all tracked snapshots."""
        self.maybe_fail()
        return tuple(self.instances.values())

    async def launch(self, spec: InstanceSpec) -> InstanceSnapshot:
        """Create one running snapshot."""
        self.maybe_fail()
        snapshot = InstanceSnapshot(
            instance_id="instance",
            name=spec.name,
            profile_id=spec.profile_id,
            status=InstanceStatus.RUNNING,
            pid=42,
            started_at="now",
        )
        self.instances[snapshot.instance_id] = snapshot
        return snapshot

    async def stop(self, instance_id: str) -> InstanceSnapshot:
        """Stop one known snapshot."""
        self.maybe_fail()
        current = self.instances[instance_id]
        stopped = InstanceSnapshot(
            instance_id=current.instance_id,
            name=current.name,
            profile_id=current.profile_id,
            status=InstanceStatus.STOPPED,
            pid=current.pid,
            started_at=current.started_at,
        )
        self.instances[instance_id] = stopped
        return stopped

    async def capture(self, instance_id: str) -> Path:
        """Create one screenshot artifact."""
        self.maybe_fail()
        path = self.root / instance_id / "screenshots" / "capture.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path

    def artifact(self, instance_id: str, relative: str) -> Path:
        """Resolve the screenshot written by capture."""
        self.maybe_fail()
        return self.root / instance_id / relative


def free_port() -> int:
    """Reserve and release one loopback port for a short test."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return cast(int, listener.getsockname()[1])


def test_real_pinned_tls_pairing_and_remote_operations(tmp_path: Path) -> None:
    """A browser identity is locally approved before every signed operation works."""

    async def run() -> None:
        agent = create_agent_identity(
            tmp_path / "identity", passphrase=_PASSPHRASE, common_name="Agent"
        )
        controller = create_ephemeral_controller_identity("Browser")
        store = AuthorizationStore(tmp_path / "approved.json")
        broker = PairingBroker(store)
        runtime = RuntimeStub(tmp_path / "artifacts")
        server = AgentApiServer(
            identity=AgentApiIdentity(agent_name="Agent", fingerprint=agent.fingerprint),
            runtime=cast(RemoteAgentRuntime, runtime),
            authorizations=store,
            pairing=broker,
            max_upload_bytes=1024 * 1024,
        )
        port = free_port()
        await server.start(
            host="127.0.0.1",
            port=port,
            ssl_context=server_ssl_context(agent, passphrase=_PASSPHRASE),
        )
        assert runtime.recovered
        with pytest.raises(RemoteApiError, match="already"):
            await server.start(
                host="127.0.0.1",
                port=port,
                ssl_context=server_ssl_context(agent, passphrase=_PASSPHRASE),
            )
        client = AgentApiClient(
            base_url=f"https://127.0.0.1:{port}/",
            fingerprint=agent.fingerprint,
            identity=controller,
        )
        code = broker.open()
        request_id, token = await client.request_pairing(code=code)
        assert await client.pairing_status(request_id, poll_token=token) == "pending"
        broker.decide(request_id, approve=True)
        assert await client.pairing_status(request_id, poll_token=token) == "approved"
        assert await client.install_profile("profile", b"bundle") == "Installed"
        launched = await client.launch(InstanceSpec(name="host", profile_id="profile"))
        assert await client.list_instances() == (launched,)
        artifact = await client.capture(launched.instance_id)
        assert artifact == "screenshots/capture.png"
        assert await client.download_artifact(launched.instance_id, artifact) == b"png"
        with (
            patch("mod_debug_pilot.infrastructure.remote_api._MAX_ARTIFACT", 2),
            pytest.raises(RemoteApiError, match="download limit"),
        ):
            await client.download_artifact(launched.instance_id, artifact)
        with pytest.raises(RemoteApiError, match="HTTP 404"):
            await client._request_bytes("GET", "/missing", signed=True)  # noqa: SLF001
        assert (await client.stop(launched.instance_id)).status is InstanceStatus.STOPPED

        conflicting = AgentApiServer(
            identity=AgentApiIdentity(agent_name="Other", fingerprint=agent.fingerprint),
            runtime=cast(RemoteAgentRuntime, RuntimeStub(tmp_path / "other")),
            authorizations=store,
            pairing=broker,
            max_upload_bytes=1024 * 1024,
        )
        with pytest.raises(OSError, match=r".+"):
            await conflicting.start(
                host="127.0.0.1",
                port=port,
                ssl_context=server_ssl_context(agent, passphrase=_PASSPHRASE),
            )
        await server.stop()
        await server.stop()

    asyncio.run(run())


def test_remote_api_identity_pairing_errors_and_unauthorized(tmp_path: Path) -> None:
    """Identity remains public while invalid pairing and unsigned calls are bounded."""

    async def run() -> None:
        agent = create_agent_identity(
            tmp_path / "identity", passphrase=_PASSPHRASE, common_name="Agent"
        )
        store = AuthorizationStore(tmp_path / "approved.json")
        broker = PairingBroker(store)
        server = AgentApiServer(
            identity=AgentApiIdentity(agent_name="Agent", fingerprint=agent.fingerprint),
            runtime=cast(RemoteAgentRuntime, RuntimeStub(tmp_path / "artifacts")),
            authorizations=store,
            pairing=broker,
            max_upload_bytes=1024 * 1024,
        )
        port = free_port()
        await server.start(
            host="127.0.0.1",
            port=port,
            ssl_context=server_ssl_context(agent, passphrase=_PASSPHRASE),
        )
        fingerprint = aiohttp.Fingerprint(bytes.fromhex(agent.fingerprint.replace(":", "")))
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://127.0.0.1:{port}/v1/identity", ssl=fingerprint
            ) as response:
                assert (await response.json())["agent_name"] == "Agent"
            async with session.post(
                f"https://127.0.0.1:{port}/v1/pairing/requests",
                json={},
                ssl=fingerprint,
            ) as response:
                assert response.status == _HTTP_FORBIDDEN
            async with session.get(
                f"https://127.0.0.1:{port}/v1/pairing/requests/missing?token=x",
                ssl=fingerprint,
            ) as response:
                assert response.status == _HTTP_NOT_FOUND
            async with session.get(
                f"https://127.0.0.1:{port}/v1/instances", ssl=fingerprint
            ) as response:
                assert response.status == _HTTP_UNAUTHORIZED
        await server.stop()

    asyncio.run(run())


def test_api_client_validation_and_response_failures() -> None:
    """Address, JSON shape, required fields, lists, transport, and artifact bounds fail."""
    identity = create_ephemeral_controller_identity("Controller")
    fingerprint = "AA:" * 31 + "AA"
    with pytest.raises(RemoteApiError, match="HTTPS"):
        AgentApiClient(base_url="http://agent", fingerprint=fingerprint, identity=identity)
    client = AgentApiClient(base_url="https://agent", fingerprint=fingerprint, identity=identity)

    async def run() -> None:
        with (
            patch.object(client, "_request_bytes", AsyncMock(return_value=b"not-json")),
            pytest.raises(RemoteApiError, match="invalid JSON"),
        ):
            await client._request("GET", "/x", signed=False)  # noqa: SLF001
        with (
            patch.object(client, "_request_bytes", AsyncMock(return_value=b"[]")),
            pytest.raises(RemoteApiError, match="response object"),
        ):
            await client._request("GET", "/x", signed=False)  # noqa: SLF001
        with (
            patch.object(client, "_request", AsyncMock(return_value={"instances": {}})),
            pytest.raises(RemoteApiError, match="instance list"),
        ):
            await client.list_instances()
        with (
            patch("mod_debug_pilot.infrastructure.remote_api._MAX_ARTIFACT", 2),
            patch.object(client, "_request_bytes", AsyncMock(return_value=b"xxx")),
            pytest.raises(RemoteApiError, match="download limit"),
        ):
            await client.download_artifact("id", "a")
        with (
            patch("aiohttp.ClientSession.request", side_effect=aiohttp.ClientError),
            pytest.raises(RemoteApiError, match="connection"),
        ):
            await client._request_bytes("GET", "/x", signed=False)  # noqa: SLF001

        class ErrorResponse:
            status = _HTTP_BAD_REQUEST

            async def read(self) -> bytes:
                return b"{}"

            async def __aenter__(self) -> ErrorResponse:
                return self

            async def __aexit__(self, *_args: object) -> None:
                pass

        class ErrorSession:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                """Ignore aiohttp session configuration."""

            async def __aenter__(self) -> ErrorSession:
                return self

            async def __aexit__(self, *_args: object) -> None:
                pass

            def request(self, *_args: object, **_kwargs: object) -> ErrorResponse:
                return ErrorResponse()

        with (
            patch("mod_debug_pilot.infrastructure.remote_api.aiohttp.ClientSession", ErrorSession),
            pytest.raises(RemoteApiError, match="HTTP 400"),
        ):
            await client._request_bytes("GET", "/x", signed=False)  # noqa: SLF001

    asyncio.run(run())


def test_api_helpers_and_error_statuses() -> None:
    """Wire helpers reject invalid JSON and map public error categories."""
    assert bundle_digest(b"x") == "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
    assert _required_string({"x": "ok"}, "x") == "ok"
    for value in (None, ""):
        with pytest.raises(RemoteApiError):
            _required_string({"x": value}, "x")
    assert _status_for(AuthenticationError()) == _HTTP_UNAUTHORIZED
    assert _status_for(ProfileImportError()) == _HTTP_BAD_REQUEST
    assert _status_for(ValueError()) == _HTTP_BAD_REQUEST
    assert _status_for(OSError()) == _HTTP_CONFLICT

    request = AsyncMock()
    request.read = AsyncMock(return_value=b"[]")
    with pytest.raises(RemoteApiError, match="object"):
        asyncio.run(_json_body(request))
    request.read = AsyncMock(return_value=b"bad")
    with pytest.raises(RemoteApiError, match="invalid"):
        asyncio.run(_json_body(request))
    request.read = AsyncMock(return_value=b"x" * (64 * 1024 + 1))
    with pytest.raises(RemoteApiError, match="large"):
        asyncio.run(_json_body(request))


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (AuthenticationError("auth"), _HTTP_UNAUTHORIZED),
        (ProfileImportError("profile"), _HTTP_BAD_REQUEST),
        (AgentRuntimeError("conflict"), _HTTP_CONFLICT),
    ],
)
def test_signed_route_failure_mapping(
    tmp_path: Path, failure: BaseException, expected_status: int
) -> None:
    """Signed runtime failures retain their public 400/401/409 categories."""

    async def run() -> None:
        agent = create_agent_identity(
            tmp_path / "identity", passphrase=_PASSPHRASE, common_name="Agent"
        )
        controller = create_ephemeral_controller_identity("Controller")
        store = AuthorizationStore(tmp_path / "approved.json")
        store.approve(
            controller_id=controller.controller_id,
            name=controller.name,
            public_key_b64=controller.public_key_b64,
        )
        runtime = RuntimeStub(tmp_path / "artifacts")
        runtime.failure = failure
        server = AgentApiServer(
            identity=AgentApiIdentity(agent_name="Agent", fingerprint=agent.fingerprint),
            runtime=cast(RemoteAgentRuntime, runtime),
            authorizations=store,
            pairing=PairingBroker(store),
            max_upload_bytes=1024 * 1024,
        )
        port = free_port()
        await server.start(
            host="127.0.0.1",
            port=port,
            ssl_context=server_ssl_context(agent, passphrase=_PASSPHRASE),
        )
        client = AgentApiClient(
            base_url=f"https://127.0.0.1:{port}",
            fingerprint=agent.fingerprint,
            identity=controller,
        )
        if isinstance(failure, ProfileImportError):
            with pytest.raises(RemoteApiError, match=str(failure)):
                await client.install_profile("profile", b"bundle")
        else:
            with pytest.raises(RemoteApiError, match=str(failure)):
                await client.list_instances()
            if isinstance(failure, AgentRuntimeError):
                operations = (
                    client.launch(InstanceSpec(name="x", profile_id="profile")),
                    client.stop("instance"),
                    client.capture("instance"),
                    client.download_artifact("instance", "x"),
                )
                for operation in operations:
                    with pytest.raises(RemoteApiError, match=str(failure)):
                        await operation
        await server.stop()
        assert expected_status == _status_for(failure)

    asyncio.run(run())
