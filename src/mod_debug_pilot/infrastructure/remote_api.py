"""Pinned-TLS HTTP API between the controller and controlled agent."""

from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import quote

import aiohttp
from aiohttp import web

from mod_debug_pilot.domain import InstanceSnapshot, InstanceSpec, normalize_fingerprint
from mod_debug_pilot.infrastructure.agent_runtime import AgentRuntimeError, RemoteAgentRuntime
from mod_debug_pilot.infrastructure.profiles import ProfileImportError
from mod_debug_pilot.infrastructure.security import (
    AuthenticationError,
    AuthorizationStore,
    ControllerIdentity,
    PairingBroker,
    signed_headers,
)

_MAX_JSON: Final = 64 * 1024
_MAX_ARTIFACT: Final = 256 * 1024 * 1024


class RemoteApiError(OSError):
    """Report a bounded remote API failure to the controller GUI."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentApiIdentity:
    """Public server identity shown before pairing."""

    agent_name: str
    fingerprint: str
    protocol_version: int = 1


class AgentApiServer:
    """Expose only profile, instance, screenshot, and artifact operations."""

    def __init__(
        self,
        *,
        identity: AgentApiIdentity,
        runtime: RemoteAgentRuntime,
        authorizations: AuthorizationStore,
        pairing: PairingBroker,
        max_upload_bytes: int,
    ) -> None:
        """Build routes without opening a socket."""
        self.identity = identity
        self.runtime = runtime
        self.authorizations = authorizations
        self.pairing = pairing
        self.max_upload_bytes = max_upload_bytes
        self._runner: web.AppRunner | None = None
        self._app = web.Application(client_max_size=max_upload_bytes)
        self._app.add_routes(
            [
                web.get("/v1/identity", self._identity),
                web.post("/v1/pairing/requests", self._pairing_request),
                web.get("/v1/pairing/requests/{request_id}", self._pairing_status),
                web.post("/v1/profiles/{profile_id}", self._install_profile),
                web.get("/v1/instances", self._instances),
                web.post("/v1/instances", self._launch),
                web.post("/v1/instances/{instance_id}/stop", self._stop),
                web.post("/v1/instances/{instance_id}/screenshots", self._screenshot),
                web.get(
                    "/v1/instances/{instance_id}/artifacts/{relative:.+}",
                    self._artifact,
                ),
            ]
        )

    async def start(self, *, host: str, port: int, ssl_context: ssl.SSLContext) -> None:
        """Recover local transactions, then start the HTTPS listener."""
        if self._runner is not None:
            raise RemoteApiError("Agent API is already running.")
        await self.runtime.recover()
        runner = web.AppRunner(self._app, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, host=host, port=port, ssl_context=ssl_context)
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner

    async def stop(self) -> None:
        """Stop accepting requests and release the listener."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _identity(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "protocol_version": self.identity.protocol_version,
                "agent_name": self.identity.agent_name,
                "fingerprint": self.identity.fingerprint,
            }
        )

    async def _pairing_request(self, request: web.Request) -> web.Response:
        try:
            payload = await _json_body(request)
            pending = self.pairing.request(
                code=_required_string(payload, name="code"),
                controller_id=_required_string(payload, name="controller_id"),
                controller_name=_required_string(payload, name="controller_name"),
                public_key_b64=_required_string(payload, name="public_key"),
            )
            return web.json_response(
                {"request_id": pending.request_id, "poll_token": pending.poll_token},
                status=202,
            )
        except (AuthenticationError, RemoteApiError) as error:
            return _error(str(error), status=403)

    async def _pairing_status(self, request: web.Request) -> web.Response:
        try:
            status = self.pairing.status(
                request.match_info["request_id"],
                poll_token=request.query.get("token", ""),
            )
        except AuthenticationError as error:
            return _error(str(error), status=404)
        value = "pending" if status is None else ("approved" if status else "rejected")
        return web.json_response({"status": value})

    async def _install_profile(self, request: web.Request) -> web.Response:
        try:
            body = await self._authorized_body(request)
            name = await self.runtime.install_profile(
                request.match_info["profile_id"],
                bundle=body,
            )
            return web.json_response({"profile_name": name}, status=201)
        except (AuthenticationError, ProfileImportError, AgentRuntimeError) as error:
            return _error(str(error), status=_status_for(error))

    async def _instances(self, request: web.Request) -> web.Response:
        try:
            await self._authorize(request, body=b"")
            instances = await self.runtime.list_instances()
            return web.json_response({"instances": [item.to_mapping() for item in instances]})
        except (AuthenticationError, AgentRuntimeError) as error:
            return _error(str(error), status=_status_for(error))

    async def _launch(self, request: web.Request) -> web.Response:
        try:
            body = await self._authorized_body(request)
            spec = InstanceSpec.from_mapping(json.loads(body))
            snapshot = await self.runtime.launch(spec)
            return web.json_response(snapshot.to_mapping(), status=201)
        except (AuthenticationError, AgentRuntimeError, ValueError, json.JSONDecodeError) as error:
            return _error(str(error), status=_status_for(error))

    async def _stop(self, request: web.Request) -> web.Response:
        try:
            await self._authorize(request, body=b"")
            snapshot = await self.runtime.stop(request.match_info["instance_id"])
            return web.json_response(snapshot.to_mapping())
        except (AuthenticationError, AgentRuntimeError) as error:
            return _error(str(error), status=_status_for(error))

    async def _screenshot(self, request: web.Request) -> web.Response:
        try:
            await self._authorize(request, body=b"")
            path = await self.runtime.capture(request.match_info["instance_id"])
            instance_root = path.parents[1]
            return web.json_response({"artifact": path.relative_to(instance_root).as_posix()})
        except (AuthenticationError, AgentRuntimeError) as error:
            return _error(str(error), status=_status_for(error))

    async def _artifact(self, request: web.Request) -> web.StreamResponse:
        try:
            await self._authorize(request, body=b"")
            path = self.runtime.artifact(
                request.match_info["instance_id"],
                relative=request.match_info["relative"],
            )
            if path.stat().st_size > _MAX_ARTIFACT:
                raise AgentRuntimeError("Artifact exceeds the download limit.")
            return web.FileResponse(path)
        except (AuthenticationError, AgentRuntimeError) as error:
            return _error(str(error), status=_status_for(error))

    async def _authorized_body(self, request: web.Request) -> bytes:
        body = await request.read()
        await self._authorize(request, body=body)
        return body

    async def _authorize(self, request: web.Request, *, body: bytes) -> None:
        self.authorizations.verify(
            controller_id=request.headers.get("X-MDP-Controller", ""),
            method=request.method,
            path=request.rel_url.path,
            body=body,
            timestamp=request.headers.get("X-MDP-Timestamp", ""),
            nonce=request.headers.get("X-MDP-Nonce", ""),
            signature_b64=request.headers.get("X-MDP-Signature", ""),
        )


class AgentApiClient:
    """Controller-side pinned TLS client with per-request Ed25519 signatures."""

    def __init__(
        self,
        *,
        base_url: str,
        fingerprint: str,
        identity: ControllerIdentity,
        timeout_seconds: float = 60.0,
    ) -> None:
        """Create a client that never falls back to CA-only trust."""
        normalized = normalize_fingerprint(fingerprint)
        self._base_url = base_url.rstrip("/")
        if not self._base_url.startswith("https://"):
            raise RemoteApiError("Agent address must use HTTPS.")
        self._fingerprint = aiohttp.Fingerprint(bytes.fromhex(normalized.replace(":", "")))
        self._identity = identity
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=10, sock_read=30)

    async def request_pairing(self, *, code: str) -> tuple[str, str]:
        """Submit this controller key using the agent-displayed one-time code."""
        payload = {
            "code": code,
            "controller_id": self._identity.controller_id,
            "controller_name": self._identity.name,
            "public_key": self._identity.public_key_b64,
        }
        response = await self._request(
            "POST",
            path="/v1/pairing/requests",
            payload=payload,
            signed=False,
        )
        return _required_string(response, name="request_id"), _required_string(
            response,
            name="poll_token",
        )

    async def pairing_status(self, request_id: str, *, poll_token: str) -> str:
        """Poll the local operator decision using the high-entropy polling secret."""
        encoded_request = quote(request_id, safe="")
        encoded_token = quote(poll_token, safe="")
        path = f"/v1/pairing/requests/{encoded_request}?token={encoded_token}"
        response = await self._request("GET", path=path, signed=False)
        return _required_string(response, name="status")

    async def install_profile(self, profile_id: str, *, bundle: bytes) -> str:
        """Upload one complete controller-built profile."""
        path = f"/v1/profiles/{quote(profile_id, safe='')}"
        response = await self._request("POST", path=path, body=bundle, signed=True)
        return _required_string(response, name="profile_name")

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Fetch all remote process records."""
        response = await self._request("GET", path="/v1/instances", signed=True)
        values = response.get("instances")
        if not isinstance(values, list):
            raise RemoteApiError("Agent returned an invalid instance list.")
        return tuple(InstanceSnapshot.from_mapping(item) for item in values)

    async def launch(self, spec: InstanceSpec) -> InstanceSnapshot:
        """Launch one allow-listed remote instance."""
        response = await self._request(
            "POST",
            path="/v1/instances",
            payload=spec.to_mapping(),
            signed=True,
        )
        return InstanceSnapshot.from_mapping(response)

    async def stop(self, instance_id: str) -> InstanceSnapshot:
        """Stop exactly one remote tracked process tree."""
        path = f"/v1/instances/{quote(instance_id, safe='')}/stop"
        response = await self._request("POST", path=path, signed=True)
        return InstanceSnapshot.from_mapping(response)

    async def capture(self, instance_id: str) -> str:
        """Ask the agent to capture its desktop and return the artifact path."""
        path = f"/v1/instances/{quote(instance_id, safe='')}/screenshots"
        response = await self._request("POST", path=path, signed=True)
        return _required_string(response, name="artifact")

    async def download_artifact(self, instance_id: str, *, relative: str) -> bytes:
        """Download one authenticated artifact with a bounded response size."""
        encoded = "/".join(quote(part, safe="") for part in Path(relative).parts)
        path = f"/v1/instances/{quote(instance_id, safe='')}/artifacts/{encoded}"
        body = await self._request_bytes("GET", path=path, signed=True)
        if len(body) > _MAX_ARTIFACT:
            raise RemoteApiError("Agent artifact exceeds the download limit.")
        return body

    async def _request(
        self,
        method: str,
        *,
        path: str,
        payload: Mapping[str, object] | None = None,
        body: bytes | None = None,
        signed: bool,
    ) -> dict[str, object]:
        resolved_body = body
        headers: dict[str, str] = {}
        if payload is not None:
            resolved_body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        resolved_body = resolved_body or b""
        response_body = await self._request_bytes(
            method,
            path=path,
            body=resolved_body,
            headers=headers,
            signed=signed,
        )
        try:
            parsed = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RemoteApiError("Agent returned invalid JSON.") from error
        if not isinstance(parsed, dict):
            raise RemoteApiError("Agent returned an invalid response object.")
        return parsed

    async def _request_bytes(
        self,
        method: str,
        *,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        signed: bool,
    ) -> bytes:
        request_path = path.split("?", 1)[0]
        resolved_headers = dict(headers or {})
        if signed:
            resolved_headers.update(
                signed_headers(
                    self._identity,
                    method=method,
                    path=request_path,
                    body=body,
                )
            )
        async with aiohttp.ClientSession(timeout=self._timeout, trust_env=False) as session:
            try:
                async with session.request(
                    method,
                    self._base_url + path,
                    data=body or None,
                    headers=resolved_headers,
                    ssl=self._fingerprint,
                    allow_redirects=False,
                ) as response:
                    response_body = await response.read()
                    if response.status < 200 or response.status >= 300:
                        message = f"Agent returned HTTP {response.status}."
                        try:
                            error = json.loads(response_body)
                            if isinstance(error, dict) and isinstance(error.get("error"), str):
                                message = error["error"]
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            pass
                        raise RemoteApiError(message)
                    return response_body
            except aiohttp.ClientError as error:
                raise RemoteApiError("Secure connection to the agent failed.") from error


async def _json_body(request: web.Request) -> dict[str, object]:
    body = await request.read()
    if len(body) > _MAX_JSON:
        raise RemoteApiError("JSON request is too large.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RemoteApiError("JSON request is invalid.") from error
    if not isinstance(payload, dict):
        raise RemoteApiError("JSON request must be an object.")
    return payload


def _required_string(payload: dict[str, object], *, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise RemoteApiError(f"Missing string field: {name}.")
    return value


def _error(message: str, *, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _status_for(error: BaseException) -> int:
    if isinstance(error, AuthenticationError):
        return 401
    if isinstance(error, (ValueError, ProfileImportError)):
        return 400
    return 409


def bundle_digest(bundle: bytes) -> str:
    """Return the transfer digest displayed by both sides."""
    return hashlib.sha256(bundle).hexdigest()
