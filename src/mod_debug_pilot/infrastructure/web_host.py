"""Run the Agent-hosted Flet Web controller on an explicit trusted-LAN boundary."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol, cast
from urllib.parse import urlsplit

import flet as ft
import uvicorn

from mod_debug_pilot.ui.web_controller import WebControllerContext, configure_web_controller

type AsgiScope = dict[str, object]
type AsgiMessage = dict[str, object]
type AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
type AsgiSend = Callable[[AsgiMessage], Awaitable[None]]

_WILDCARD_HOSTS = frozenset(("0.0.0.0", "::"))  # noqa: S104 - Values are compared, not bound.
_IPV4_VERSION = 4
_HEADER_PAIR_SIZE = 2


class AsgiApplication(Protocol):
    """Minimal ASGI callable contract used by the local guard."""

    async def __call__(  # noqa: PLR0917 -- keyword-only-exception: ASGI invokes application callables positionally.
        self,
        scope: AsgiScope,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        """Handle one ASGI connection."""


class WebHostError(OSError):
    """Report failure to start or stop the browser controller listener."""


class TrustedLanGuard:
    """Reject DNS-rebinding and cross-origin HTTP/WebSocket connections."""

    def __init__(self, *, app: AsgiApplication, allowed_hosts: Iterable[str]) -> None:
        """Wrap an ASGI application with an exact normalized host allow-list."""
        normalized = {_normalize_host(authority=item) for item in allowed_hosts}
        self._allowed_hosts = frozenset(item for item in normalized if item is not None)
        if not self._allowed_hosts:
            raise WebHostError("Controller Web UI requires at least one allowed LAN host.")
        self._app = app

    async def __call__(  # noqa: PLR0917 -- keyword-only-exception: ASGI invokes application callables positionally.
        self,
        scope: AsgiScope,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        """Forward only same-origin requests for an explicitly allowed host."""
        if _scope_allowed(scope=scope, allowed_hosts=self._allowed_hosts):
            await self._app(scope, receive, send)
            return
        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 421,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": b"Misdirected request."})


class FletWebHost:
    """Own one plain-HTTP Flet server restricted to known Agent LAN names."""

    def __init__(
        self,
        *,
        context: WebControllerContext,
        allowed_hosts: Iterable[str],
    ) -> None:
        """Create a stopped trusted-LAN host around Agent-owned services."""

        async def page_main(page: ft.Page) -> None:  # noqa: PLR0917 -- keyword-only-exception: Flet invokes page entrypoints positionally.
            await configure_web_controller(page, context=context)

        app = cast(
            AsgiApplication,
            ft.run(
                page_main,
                name="controller",
                view=ft.AppView.WEB_BROWSER,
                export_asgi_app=True,
                no_cdn=True,
            ),
        )
        self._app = TrustedLanGuard(app=app, allowed_hosts=allowed_hosts)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self, *, host: str, port: int) -> None:
        """Start plain HTTP and wait until the socket is accepting connections."""
        if self._task is not None:
            raise WebHostError("Controller Web UI is already running.")
        config = uvicorn.Config(
            self._app,
            host=host,
            port=port,
            access_log=False,
            log_level="warning",
            server_header=False,
            date_header=False,
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(), name="moddebugpilot-controller-web")
        self._server = server
        self._task = task
        for _attempt in range(100):
            if server.started:
                return
            if task.done():
                break
            await asyncio.sleep(0.05)
        await self.stop()
        raise WebHostError("Controller Web UI could not start.")

    async def stop(self) -> None:
        """Request graceful shutdown and await completion."""
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task
        self._server = None
        self._task = None


def discover_controller_hosts(*, bind_host: str) -> tuple[str, ...]:
    """Return exact host names and addresses accepted by the HTTP listener."""
    hosts = {"localhost", "127.0.0.1", "::1"}
    normalized_bind = _normalize_host(authority=bind_host)
    if normalized_bind is not None and normalized_bind not in _WILDCARD_HOSTS:
        hosts.add(normalized_bind)
    names = {socket.gethostname(), socket.getfqdn()}
    hosts.update(name for name in names if name)
    for name in tuple(names):
        if not name:
            continue
        try:
            hosts.update(
                address
                for item in socket.getaddrinfo(name, None)
                if isinstance((address := item[4][0]), str)
            )
        except OSError:
            continue
    return tuple(sorted(hosts, key=_host_sort_key))


def preferred_controller_host(*, hosts: Iterable[str]) -> str:
    """Prefer a non-loopback IPv4 address for the copyable browser URL."""
    values = tuple(hosts)
    for value in values:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if (
            address.version == _IPV4_VERSION
            and not address.is_loopback
            and not address.is_unspecified
        ):
            return value
    for value in values:
        if value not in {"localhost", "127.0.0.1", "::1", *_WILDCARD_HOSTS}:
            return value
    return "127.0.0.1"


def controller_http_url(*, host: str, port: int) -> str:
    """Format one IPv4, IPv6, or DNS controller URL."""
    normalized = _normalize_host(authority=host)
    if normalized is None:
        raise WebHostError("Controller URL host is invalid.")
    rendered = f"[{normalized}]" if ":" in normalized else normalized
    return f"http://{rendered}:{port}/controller"


def _scope_allowed(*, scope: AsgiScope, allowed_hosts: frozenset[str]) -> bool:
    scope_type = scope.get("type")
    if scope_type not in {"http", "websocket", "lifespan"}:
        return False
    if scope_type == "lifespan":
        return True
    headers = _headers(scope=scope)
    authority = headers.get("host")
    request_authority = _normalize_authority(authority=authority or "")
    request_host = None if request_authority is None else request_authority[0]
    if request_host not in allowed_hosts:
        return False
    origin = headers.get("origin")
    if origin is None:
        return scope_type == "http"
    parsed = urlsplit(origin)
    return (
        parsed.scheme == "http"
        and _normalize_authority(authority=parsed.netloc) == request_authority
    )


def _headers(*, scope: AsgiScope) -> dict[str, str]:
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, list):
        return {}
    result: dict[str, str] = {}
    for item in raw_headers:
        if not isinstance(item, tuple) or len(item) != _HEADER_PAIR_SIZE:
            continue
        name, value = item
        if isinstance(name, bytes) and isinstance(value, bytes):
            result[name.decode("latin-1").casefold()] = value.decode("latin-1")
    return result


def _normalize_host(*, authority: str) -> str | None:
    parsed = _normalize_authority(authority=authority)
    return None if parsed is None else parsed[0]


def _normalize_authority(*, authority: str) -> tuple[str, int | None] | None:
    value = authority.strip()
    if (
        not value
        or "@" in value
        or "\\" in value
        or "/" in value
        or any(character.isspace() for character in value)
    ):
        return None
    if value.count(":") > 1 and not value.startswith("["):
        return _normalize_raw_ip(value=value)
    try:
        parsed = urlsplit(f"//{value}")
        host = parsed.hostname
        if host is None:
            return None
        port = parsed.port
    except ValueError:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not all(
            part and part.replace("-", "").isalnum() for part in host.rstrip(".").split(".")
        ):
            return None
    return host.rstrip(".").casefold(), port


def _normalize_raw_ip(
    *, value: str
) -> (
    tuple[str, None] | None
):  # keyword-only-exception: sorted key callbacks receive their value positionally.
    try:
        return ipaddress.ip_address(value).compressed.casefold(), None
    except ValueError:
        return None


# keyword-only-exception: sorted key callbacks receive their value positionally.
def _host_sort_key(value: str) -> tuple[int, str]:  # noqa: PLR0917 -- keyword-only-exception: sorted invokes key callbacks positionally.
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return 2, value.casefold()
    if address.version == _IPV4_VERSION and not address.is_loopback:
        return 0, value
    return 1, value
