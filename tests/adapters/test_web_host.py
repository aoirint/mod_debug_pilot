"""Flet ASGI export, trusted-LAN guard, and plain-HTTP host lifecycle tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import flet as ft
import pytest

from mod_debug_pilot.infrastructure.web_host import (
    AsgiMessage,
    AsgiReceive,
    AsgiScope,
    AsgiSend,
    FletWebHost,
    TrustedLanGuard,
    WebHostError,
    controller_http_url,
    discover_controller_hosts,
    preferred_controller_host,
)

_MISDIRECTED_STATUS = 421


class ServerStub:
    """Configurable Uvicorn server lifecycle."""

    def __init__(self, *, _config: object, starts: bool = True, waits: bool = False) -> None:
        """Configure start visibility and optional shutdown wait."""
        self.starts = starts
        self.waits = waits
        self.started = False
        self._should_exit = False
        self._exit = asyncio.Event()

    @property
    def should_exit(
        self,
    ) -> bool:  # keyword-only-exception: Python property setters receive values positionally.
        """Return whether shutdown was requested."""
        return self._should_exit

    @should_exit.setter
    def should_exit(self, value: bool) -> None:  # noqa: PLR0917 -- keyword-only-exception: property and ASGI callbacks preserve the external signature.
        """Wake the server when shutdown is requested."""
        self._should_exit = value
        if value:
            self._exit.set()

    async def serve(self) -> None:
        """Expose started state and optionally wait for should-exit."""
        self.started = self.starts
        if self.waits:
            await self._exit.wait()


class AsgiStub:
    """Record scopes forwarded through the trusted-LAN guard."""

    def __init__(self) -> None:
        """Create an empty call ledger."""
        self.scopes: list[AsgiScope] = []

    async def __call__(  # noqa: PLR0917 -- keyword-only-exception: property and ASGI callbacks preserve the external signature.
        self,
        scope: AsgiScope,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        """Record one accepted connection."""
        del receive, send
        self.scopes.append(scope)


class MessageSink:
    """Collect ASGI messages without loop-variable closures."""

    def __init__(self) -> None:
        """Create an empty message list."""
        self.messages: list[AsgiMessage] = []

    async def __call__(self, message: AsgiMessage) -> None:  # noqa: PLR0917 -- keyword-only-exception: property and ASGI callbacks preserve the external signature.
        """Append one message."""
        self.messages.append(message)


def test_web_host_exports_controller_and_starts_stops() -> None:
    """The exported page callback and plain-HTTP lifecycle are owned."""

    async def run() -> None:
        exported: dict[str, object] = {}

        def fake_run(page_main: object, **kwargs: object) -> object:  # noqa: PLR0917 -- keyword-only-exception: Flet run callback ABI.
            exported["page_main"] = page_main
            exported.update(kwargs)
            return AsgiStub()

        server = ServerStub(_config=Mock())
        page_main = AsyncMock()
        with (
            patch("mod_debug_pilot.infrastructure.web_host.ft.run", side_effect=fake_run),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Config") as config,
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Server", return_value=server),
        ):
            host = FletWebHost(page_main=page_main, allowed_hosts=("127.0.0.1",))
            callback = cast(Callable[[ft.Page], Awaitable[None]], exported["page_main"])
            await callback(cast(ft.Page, Mock()))
            page_main.assert_awaited_once()
            assert exported["no_cdn"] is True
            await host.start(host="127.0.0.1", port=48951)
            assert "ssl_certfile" not in config.call_args.kwargs
            with pytest.raises(WebHostError, match="already"):
                await host.start(host="127.0.0.1", port=48951)
            await host.stop()
            assert server.should_exit
            await host.stop()

    asyncio.run(run())


def test_web_host_reports_early_exit_and_timeout() -> None:
    """Both an exited server and one that never becomes ready fail visibly."""

    async def run() -> None:
        with (
            patch("mod_debug_pilot.infrastructure.web_host.ft.run", return_value=AsgiStub()),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Config"),
            patch(
                "mod_debug_pilot.infrastructure.web_host.uvicorn.Server",
                return_value=ServerStub(_config=Mock(), starts=False),
            ),
        ):
            host = FletWebHost(page_main=AsyncMock(), allowed_hosts=("localhost",))
            with pytest.raises(WebHostError, match="could not start"):
                await host.start(host="127.0.0.1", port=48951)

        waiting = ServerStub(_config=Mock(), starts=False, waits=True)
        original_sleep = asyncio.sleep

        async def fast_sleep(  # keyword-only-exception: asyncio callback ABI.
            _seconds: float,
        ) -> None:
            await original_sleep(0)

        with (
            patch("mod_debug_pilot.infrastructure.web_host.ft.run", return_value=AsgiStub()),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Config"),
            patch("mod_debug_pilot.infrastructure.web_host.uvicorn.Server", return_value=waiting),
            patch("mod_debug_pilot.infrastructure.web_host.asyncio.sleep", side_effect=fast_sleep),
        ):
            host = FletWebHost(page_main=AsyncMock(), allowed_hosts=("localhost",))
            with pytest.raises(WebHostError, match="could not start"):
                await host.start(host="127.0.0.1", port=48951)
            assert waiting.should_exit

    asyncio.run(run())


def test_trusted_lan_guard_accepts_exact_http_and_websocket_origins() -> None:
    """Exact allowed Host and same-origin HTTP/WebSocket connections are forwarded."""

    async def run() -> None:
        app = AsgiStub()
        guard = TrustedLanGuard(app=app, allowed_hosts=("Agent.local", "192.168.1.8"))
        sent: list[AsgiMessage] = []

        async def receive() -> (
            AsgiMessage
        ):  # keyword-only-exception: ASGI invokes send callbacks positionally.
            return {}

        async def send(message: AsgiMessage) -> None:  # noqa: PLR0917 -- keyword-only-exception: property and ASGI callbacks preserve the external signature.
            sent.append(message)

        scopes: tuple[AsgiScope, ...] = (
            {"type": "lifespan"},
            {"type": "http", "headers": [(b"host", b"agent.local:48951")]},
            {
                "type": "websocket",
                "headers": [
                    (b"host", b"192.168.1.8:48951"),
                    (b"origin", b"http://192.168.1.8:48951"),
                ],
            },
        )
        for scope in scopes:
            await guard(scope, receive, send)

        assert app.scopes == list(scopes)
        assert sent == []

    asyncio.run(run())


def test_trusted_lan_guard_rejects_bad_host_origin_and_scope() -> None:
    """Unknown hosts, cross-origin sockets, and malformed scopes fail before Flet."""

    async def run() -> None:
        app = AsgiStub()
        guard = TrustedLanGuard(app=app, allowed_hosts=("agent.local",))

        async def receive() -> AsgiMessage:
            return {}

        cases: tuple[AsgiScope, ...] = (
            {"type": "http", "headers": [(b"host", b"evil.test")]},
            {"type": "http", "headers": "invalid"},
            {
                "type": "http",
                "headers": [b"not-a-pair", ("host", "agent.local"), (b"host", b"evil.test")],
            },
            {"type": "smtp", "headers": []},
            {"type": "websocket", "headers": [(b"host", b"agent.local:48951")]},
            {
                "type": "websocket",
                "headers": [
                    (b"host", b"agent.local:48951"),
                    (b"origin", b"https://agent.local:48951"),
                ],
            },
            {
                "type": "websocket",
                "headers": [
                    (b"host", b"agent.local:48951"),
                    (b"origin", b"http://agent.local:48952"),
                ],
            },
        )
        sent_by_case: list[list[AsgiMessage]] = []
        for scope in cases:
            sink = MessageSink()
            await guard(scope, receive, sink)
            sent_by_case.append(sink.messages)

        assert app.scopes == []
        assert sent_by_case[0][0]["status"] == _MISDIRECTED_STATUS
        assert sent_by_case[0][1]["body"] == b"Misdirected request."
        assert sent_by_case[4] == [{"type": "websocket.close", "code": 1008}]

    asyncio.run(run())


def test_controller_host_discovery_url_and_validation() -> None:
    """LAN discovery prefers IPv4 and URL formatting handles DNS and IPv6 safely."""
    addresses = [
        (socket_family, None, None, None, (address, 0))
        for socket_family, address in ((2, "192.168.1.8"), (23, "fe80::1"))
    ]
    with (
        patch("mod_debug_pilot.infrastructure.web_host.socket.gethostname", return_value="Agent"),
        patch("mod_debug_pilot.infrastructure.web_host.socket.getfqdn", return_value="agent.local"),
        patch(
            "mod_debug_pilot.infrastructure.web_host.socket.getaddrinfo",
            side_effect=[addresses, OSError("unresolved")],
        ),
    ):
        hosts = discover_controller_hosts(bind_host="0.0.0.0")  # noqa: S104 - Discovery fixture.
    assert "192.168.1.8" in hosts
    assert preferred_controller_host(hosts=hosts) == "192.168.1.8"
    assert controller_http_url(host="agent.local", port=48951) == (
        "http://agent.local:48951/controller"
    )
    assert controller_http_url(host="fe80::1", port=48951) == "http://[fe80::1]:48951/controller"
    assert preferred_controller_host(hosts=("localhost", "agent.local")) == "agent.local"
    assert preferred_controller_host(hosts=("localhost", "::1")) == "127.0.0.1"
    assert "10.0.0.2" in discover_controller_hosts(bind_host="10.0.0.2")
    with (
        patch("mod_debug_pilot.infrastructure.web_host.socket.gethostname", return_value=""),
        patch("mod_debug_pilot.infrastructure.web_host.socket.getfqdn", return_value="agent"),
        patch("mod_debug_pilot.infrastructure.web_host.socket.getaddrinfo", return_value=[]),
    ):
        assert "agent" in discover_controller_hosts(bind_host="0.0.0.0")  # noqa: S104

    for value in (
        "",
        ":",
        "::::",
        "bad host",
        "bad_name",
        "user@host",
        "bad/name",
        "[broken",
        "host:bad",
    ):
        with pytest.raises(WebHostError):
            controller_http_url(host=value, port=48951)
    with pytest.raises(WebHostError, match="at least one"):
        TrustedLanGuard(app=AsgiStub(), allowed_hosts=("bad host",))
