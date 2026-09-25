from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from websockets.asyncio.server import serve as websocket_serve
from websockets.datastructures import Headers
from websockets.exceptions import InvalidHandshake, InvalidProxy, InvalidStatus
from websockets.http11 import Response

import app.core.clients.proxy as proxy_module
import app.core.clients.proxy_websocket as proxy_websocket_module
from app.core.clients.codex import CodexTransportError, CodexWebSocketResult
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import connect_responses_websocket
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute


def _proxy_error_code(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("code")


def _proxy_error_message(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("message")


def _proxy_error_type(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("type")


class _UnexpectedAiohttpSession:
    async def ws_connect(self, *args, **kwargs):  # pragma: no cover - red-path guard
        raise AssertionError("aiohttp ws_connect should not be used for upstream websocket transport")


class _UnexpectedHttpClient:
    websocket_session = _UnexpectedAiohttpSession()


class _FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return '{"type":"response.completed"}'

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


async def _local_proxy_tunnel_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    proxy_hits: list[str],
) -> None:
    target_writer: asyncio.StreamWriter | None = None
    try:
        request_line = await reader.readline()
        method, target, _version = request_line.decode("ascii").strip().split(" ", 2)
        assert method == "CONNECT"
        host, port_text = target.rsplit(":", 1)
        proxy_hits.append(target)

        while await reader.readline() != b"\r\n":
            pass

        target_reader, target_writer = await asyncio.open_connection(host, int(port_text))
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        async def relay(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass

        relays = [
            asyncio.create_task(relay(reader, target_writer)),
            asyncio.create_task(relay(target_reader, writer)),
        ]
        try:
            await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in relays:
                task.cancel()
            await asyncio.gather(*relays, return_exceptions=True)
    finally:
        writer.close()
        if target_writer is not None:
            target_writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
        if target_writer is not None:
            with contextlib.suppress(ConnectionError):
                await target_writer.wait_closed()


class _FakeCodexWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self.response = SimpleNamespace(headers={"x-codex-turn-state": "turn-routed"})

    async def send_str(self, data: str) -> None:
        del data

    async def send_bytes(self, data: bytes) -> None:
        del data

    async def recv(self) -> tuple[bytes, int]:
        return b'{"type":"response.completed"}', 1

    async def close(self) -> None:
        self.closed = True


class _FakeCodexClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.websocket = _FakeCodexWebSocket()

    async def open_ws_with_route_metadata(
        self,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        **kwargs: object,
    ) -> CodexWebSocketResult:
        self.calls.append({"url": url, "route": route, **kwargs})
        return CodexWebSocketResult(
            websocket=self.websocket,
            context=None,
            route=route,
            fallback_used=False,
        )

    async def close(self) -> None:
        return None


class _FailingCodexClient:
    def __init__(self) -> None:
        self.closed = False

    async def open_ws_with_route_metadata(
        self,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        **kwargs: object,
    ) -> CodexWebSocketResult:
        del url, route, kwargs
        raise CodexTransportError("Codex upstream websocket failed via proxy endpoint ep_1: OSError")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_websockets_transport(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {
            "openai-beta": "responses_websockets=2026-02-06",
            "session_id": "session-1",
            "User-Agent": "Codex CLI Test",
            "Origin": "https://chatgpt.com",
            "Cookie": "dashboard_session=secret",
        },
        "access-token",
        "account-123",
        allow_direct_egress=True,
    )

    await websocket.send_text("hello")

    assert fake_connection.sent == ["hello"]
    assert seen["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["origin"] == "https://chatgpt.com"
    assert kwargs["user_agent_header"] == "Codex CLI Test"
    assert kwargs["proxy"] is None
    assert kwargs["open_timeout"] == 7.0
    assert "ping_interval" not in kwargs
    assert kwargs["ping_timeout"] is None
    assert kwargs["max_size"] == 4321
    assert kwargs["compression"] is None
    assert kwargs["ssl"] is proxy_websocket_module._shared_upstream_ssl_context()
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    assert additional_headers["Authorization"] == "Bearer access-token"
    assert additional_headers["chatgpt-account-id"] == "account-123"
    assert additional_headers["openai-beta"] == "responses_websockets=2026-02-06"
    assert additional_headers["session_id"] == "session-1"
    assert "Cookie" not in additional_headers
    assert "User-Agent" not in additional_headers
    assert "Origin" not in additional_headers


@pytest.mark.asyncio
async def test_connect_responses_websocket_routed_codex_call_preserves_size_limit(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    codex_client = _FakeCodexClient()
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {
            "openai-beta": "responses_websockets=2026-02-06",
            "User-Agent": "Codex CLI Test",
            "Origin": "https://chatgpt.com",
        },
        "access-token",
        "account-123",
        route=route,
        codex_client=cast(Any, codex_client),
    )
    await websocket.close()

    assert codex_client.calls
    call = codex_client.calls[0]
    assert call["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    assert call["route"] is route
    assert call["timeout"] == 7.0
    assert call["max_message_size"] == 4321
    assert "max_size" not in call
    assert websocket.response_header("x-codex-turn-state") == "turn-routed"


@pytest.mark.asyncio
async def test_connect_responses_websocket_routed_transport_error_maps_proxy_error(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            route=route,
            codex_client=cast(Any, _FailingCodexClient()),
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    assert "ep_1" in (_proxy_error_message(exc_info.value) or "")


@pytest.mark.asyncio
async def test_connect_responses_websocket_appends_required_beta_header(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    await connect_responses_websocket(
        {"OpenAI-Beta": "assistants=v2"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    assert additional_headers["OpenAI-Beta"] == "assistants=v2, responses_websockets=2026-02-06"


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_invalid_status(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        raise InvalidStatus(
            Response(
                403,
                "Forbidden",
                Headers({"Content-Type": "application/json"}),
                b'{"error":{"message":"Forbidden","type":"permission_error","code":"forbidden"}}',
            )
        )

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 403
    assert _proxy_error_code(exc_info.value) == "forbidden"
    assert _proxy_error_type(exc_info.value) == "permission_error"


@pytest.mark.asyncio
async def test_connect_responses_websocket_can_opt_in_to_env_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7891")

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_disables_proxy_when_env_proxy_is_unset(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in (
        "no_proxy",
        "NO_PROXY",
        "wss_proxy",
        "WSS_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "socks_proxy",
        "SOCKS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_all_proxy_fallback(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("socks_proxy", raising=False)
    monkeypatch.delenv("SOCKS_PROXY", raising=False)
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7890")
    monkeypatch.delenv("ALL_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_socks_proxy_before_all_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("wss_proxy", raising=False)
    monkeypatch.delenv("WSS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("socks_proxy", "socks5://127.0.0.1:7890")
    monkeypatch.delenv("SOCKS_PROXY", raising=False)
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7891")
    monkeypatch.delenv("ALL_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_socks_proxy_before_https_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("wss_proxy", raising=False)
    monkeypatch.delenv("WSS_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("socks_proxy", "socks5://127.0.0.1:7891")
    monkeypatch.delenv("SOCKS_PROXY", raising=False)
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7892")
    monkeypatch.delenv("ALL_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5://127.0.0.1:7891"


@pytest.mark.asyncio
async def test_connect_responses_websocket_normalizes_http_socks_env_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("socks_proxy", "http://127.0.0.1:7891")
    monkeypatch.delenv("SOCKS_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5h://127.0.0.1:7891"


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_settings_proxy_env(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    class _Settings(SimpleNamespace):
        def upstream_websocket_proxy_env(self):
            return {"https_proxy": "http://127.0.0.1:7890"}

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: _Settings(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in ("https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_respects_settings_no_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    class _Settings(SimpleNamespace):
        def upstream_websocket_proxy_env(self):
            return {
                "https_proxy": "http://127.0.0.1:7890",
                "no_proxy": "chatgpt.com",
            }

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: _Settings(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in ("https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_https_proxy_fallback_for_ws(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="http://chatgpt.local/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("ws_proxy", raising=False)
    monkeypatch.delenv("WS_PROXY", raising=False)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7889")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7891")

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert seen["url"] == "ws://chatgpt.local/backend-api/codex/responses"
    assert kwargs["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_traverses_http_proxy_smoke(monkeypatch):
    async def upstream_handler(connection):
        assert await connection.recv() == "hello"
        await connection.send('{"type":"response.completed"}')

    proxy_hits: list[str] = []

    async with websocket_serve(upstream_handler, "127.0.0.1", 0) as upstream_server:
        upstream_socket = next(iter(upstream_server.sockets))
        upstream_port = upstream_socket.getsockname()[1]
        proxy_server = await asyncio.start_server(
            lambda reader, writer: _local_proxy_tunnel_handler(reader, writer, proxy_hits),
            "127.0.0.1",
            0,
        )
        async with proxy_server:
            proxy_port = proxy_server.sockets[0].getsockname()[1]
            monkeypatch.delenv("no_proxy", raising=False)
            monkeypatch.delenv("NO_PROXY", raising=False)
            monkeypatch.delenv("ws_proxy", raising=False)
            monkeypatch.delenv("WS_PROXY", raising=False)
            monkeypatch.delenv("http_proxy", raising=False)
            monkeypatch.delenv("HTTP_PROXY", raising=False)
            monkeypatch.setenv("https_proxy", f"http://127.0.0.1:{proxy_port}")
            monkeypatch.delenv("all_proxy", raising=False)
            monkeypatch.delenv("ALL_PROXY", raising=False)
            monkeypatch.setattr(
                proxy_websocket_module,
                "get_settings",
                lambda: SimpleNamespace(
                    upstream_base_url=f"http://127.0.0.1:{upstream_port}/backend-api",
                    upstream_connect_timeout_seconds=7.0,
                    max_sse_event_bytes=4321,
                    upstream_websocket_trust_env=True,
                ),
            )

            websocket = await connect_responses_websocket(
                {"openai-beta": "responses_websockets=2026-02-06"},
                "access-token",
                None,
                allow_direct_egress=True,
            )
            await websocket.send_text("hello")
            message = await websocket.receive()
            await websocket.close()

    assert message.kind == "text"
    assert message.text == '{"type":"response.completed"}'
    assert proxy_hits == [f"127.0.0.1:{upstream_port}"]


@pytest.mark.asyncio
async def test_connect_responses_websocket_ignores_cgi_http_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="http://chatgpt.local/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in (
        "no_proxy",
        "NO_PROXY",
        "ws_proxy",
        "WS_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "http_proxy",
        "socks_proxy",
        "SOCKS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REQUEST_METHOD", "GET")
    monkeypatch.setenv("HTTP_PROXY", "http://attacker.invalid:8080")

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert seen["url"] == "ws://chatgpt.local/backend-api/codex/responses"
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_generic_invalid_handshake(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidHandshake("proxy CONNECT failed")

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    assert _proxy_error_message(exc_info.value) == "proxy CONNECT failed"


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_invalid_proxy(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidProxy("http://proxy.invalid", "unsupported proxy scheme")

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"


# The upstream edge rejects websocket handshakes with a bare 403 (no JSON error
# body) when too many open from one address in a short time: live evals on
# 2026-09-25 saw it hit every account in the same second at 100+ parallel agents,
# and a burst could stay rejected for over a minute. That is a rate limit, so the
# handshake is retried with backoff for minutes, not a fixed handful of tries. A
# 403 carrying an OpenAI error body is about the account and must still surface on
# the first answer.
async def _serve_handshakes(monkeypatch, answers: list[tuple[int, bytes, str] | None]):
    attempts: list[int] = []

    def process_request(connection, request):
        attempts.append(len(attempts))
        answer = answers[min(len(attempts) - 1, len(answers) - 1)]
        if answer is None:
            return None
        status, body, content_type = answer
        return Response(status, "Forbidden", Headers({"Content-Type": content_type}), body)

    async def upstream_handler(connection):
        await connection.send('{"type":"response.completed"}')

    server = await websocket_serve(upstream_handler, "127.0.0.1", 0, process_request=process_request).__aenter__()
    port = next(iter(server.sockets)).getsockname()[1]
    monkeypatch.setattr(proxy_module, "_EDGE_REJECT_FIRST_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(proxy_module, "_EDGE_REJECT_MAX_BACKOFF_SECONDS", 0.02)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url=f"http://127.0.0.1:{port}/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )
    return server, attempts


_EDGE_403 = (403, b"<html><body>Forbidden</body></html>", "text/html")
_ACCOUNT_403 = (
    403,
    b'{"error":{"message":"account deactivated","type":"permission_error","code":"account_deactivated"}}',
    "application/json",
)


@pytest.mark.asyncio
async def test_edge_rejected_handshake_is_retried_until_it_connects(monkeypatch):
    # Eight rejections in a row: at 200 parallel agents the edge kept rejecting
    # past the six tries (about 30 s) the first version allowed.
    server, attempts = await _serve_handshakes(monkeypatch, [_EDGE_403] * 8 + [None])
    try:
        websocket = await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"}, "access-token", None, allow_direct_egress=True
        )
        message = await websocket.receive()
        await websocket.close()
    finally:
        server.close()
        await server.wait_closed()

    assert len(attempts) == 9
    assert message.text == '{"type":"response.completed"}'


@pytest.mark.asyncio
async def test_edge_rejection_that_never_clears_surfaces_after_bounded_retries(monkeypatch):
    server, attempts = await _serve_handshakes(monkeypatch, [_EDGE_403])
    monkeypatch.setattr(proxy_module, "_EDGE_REJECT_RETRY_WINDOW_SECONDS", 0.3)
    started_at = time.monotonic()
    try:
        with pytest.raises(ProxyResponseError) as exc_info:
            await connect_responses_websocket(
                {"openai-beta": "responses_websockets=2026-02-06"}, "access-token", None, allow_direct_egress=True
            )
    finally:
        server.close()
        await server.wait_closed()

    assert exc_info.value.status_code == 403
    assert len(attempts) > 1
    assert time.monotonic() - started_at < 5.0


@pytest.mark.asyncio
async def test_account_forbidden_handshake_surfaces_without_retry(monkeypatch):
    server, attempts = await _serve_handshakes(monkeypatch, [_ACCOUNT_403, None])
    try:
        with pytest.raises(ProxyResponseError) as exc_info:
            await connect_responses_websocket(
                {"openai-beta": "responses_websockets=2026-02-06"}, "access-token", None, allow_direct_egress=True
            )
    finally:
        server.close()
        await server.wait_closed()

    assert len(attempts) == 1
    assert _proxy_error_code(exc_info.value) == "account_deactivated"
