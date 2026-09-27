from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import aiohttp
import pytest
from curl_cffi.requests.exceptions import RequestException

from app.core.anthropic.oauth import refresh_anthropic_access_token
from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, refresh_access_token
from app.core.clients.codex import CodexClient
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, *, status: int = 200, error: BaseException | None = None) -> None:
        self.status = status
        self._error = error

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def json(self, **kwargs: object) -> dict[str, object]:
        if self._error:
            raise self._error
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}


class _Session:
    def __init__(self, outcome: _Response | BaseException) -> None:
        self._outcome = outcome

    def post(self, *args: object, **kwargs: object) -> _Response:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("connect", ExchangePhase.PRE_SEND),
        ("read", ExchangePhase.AMBIGUOUS),
        ("body", ExchangePhase.AMBIGUOUS),
    ],
)
async def test_aiohttp_exchange_phase(provider: str, outcome: str, expected: ExchangePhase) -> None:
    if outcome == "connect":
        failure = aiohttp.ClientConnectorError(
            SimpleNamespace(host="example.invalid", port=443, ssl=True), OSError("refused")
        )
        session = _Session(failure)
    else:
        failure = asyncio.TimeoutError("read timeout") if outcome == "read" else ValueError("truncated body")
        session = _Session(_Response(error=failure))
    with pytest.raises(RefreshError) as error:
        if provider == "anthropic":
            await refresh_anthropic_access_token("old-refresh", session=cast(aiohttp.ClientSession, session))
        else:
            await refresh_access_token(
                "old-refresh", session=cast(aiohttp.ClientSession, session), allow_direct_egress=True
            )
    assert error.value.phase == expected


class _CurlSession:
    def __init__(self, code: int) -> None:
        self.code = code

    async def request(self, method: str, url: str, **kwargs: Any) -> object:
        raise RequestException("transport failure", code=self.code)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,expected",
    [
        (5, ExchangePhase.PRE_SEND),
        (6, ExchangePhase.PRE_SEND),
        (7, ExchangePhase.PRE_SEND),
        (35, ExchangePhase.PRE_SEND),
        (60, ExchangePhase.PRE_SEND),
        (28, ExchangePhase.AMBIGUOUS),
    ],
)
async def test_routed_curl_error_phase(code: int, expected: ExchangePhase) -> None:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool",
        endpoint=ResolvedProxyEndpoint("endpoint", "http", "proxy.invalid", 8080),
        fallbacks=(),
    )
    with pytest.raises(RefreshError) as error:
        await refresh_access_token("old-refresh", route=route, codex_client=CodexClient(_CurlSession(code)))
    assert error.value.phase == expected
