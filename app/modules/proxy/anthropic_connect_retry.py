from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import AsyncContextManager

import aiohttp

from app.core.clients import upstream_body
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError
from app.modules.proxy.anthropic_retry_backoff import connect_backoff_seconds

logger = logging.getLogger(__name__)

CONNECT_ERRORS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectorError,
    aiohttp.ServerTimeoutError,
    aiohttp.ClientOSError,
    asyncio.TimeoutError,
)


class UpstreamStallTimeout(Exception):
    def __init__(self, phase: str, seconds: float) -> None:
        self.phase = phase
        self.seconds = seconds
        self.code = f"upstream_{phase}_timeout"
        if phase == "first_byte":
            message = f"Anthropic sent no response bytes within {seconds:g} s ({self.code})"
        else:
            message = f"Anthropic stream went silent for {seconds:g} s ({self.code})"
        super().__init__(message)


def compression_deadline_error() -> AnthropicProxyError:
    return AnthropicProxyError(504, "Proxy request budget exhausted", code="upstream_request_timeout")


class ConnectRetryingResponse:
    def __init__(
        self,
        open_response: Callable[[], AsyncContextManager[aiohttp.ClientResponse]],
        *,
        attempts: int,
        label: str,
        first_byte_deadline: float | None = None,
        first_byte_seconds: float = 0.0,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._open_response = open_response
        self._attempts = max(1, attempts)
        self._label = label
        self._first_byte_deadline = first_byte_deadline
        self._first_byte_seconds = first_byte_seconds
        self._sleep_fn = sleep_fn
        self._context: AsyncContextManager[aiohttp.ClientResponse] | None = None

    async def __aenter__(self) -> aiohttp.ClientResponse:
        bound = asyncio.timeout_at(self._first_byte_deadline)
        response: aiohttp.ClientResponse | None = None
        try:
            async with bound:
                response = await self._open_with_retries()
                if self._first_byte_deadline is not None and response.status >= 400:
                    await response.read()
                return response
        except BaseException as exc:
            if response is not None:
                response.close()
                self._context = None
            if isinstance(exc, TimeoutError) and bound.expired():
                raise UpstreamStallTimeout("first_byte", self._first_byte_seconds) from None
            raise

    async def _open_with_retries(self) -> aiohttp.ClientResponse:
        last_error: BaseException | None = None
        for attempt in range(self._attempts):
            context = self._open_response()
            try:
                response = await context.__aenter__()
            except upstream_body.CompressionDeadlineExceeded as exc:
                raise compression_deadline_error() from exc
            except CONNECT_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "upstream_connect_failed provider=%s attempt=%d/%d error=%s",
                    self._label,
                    attempt + 1,
                    self._attempts,
                    exc,
                )
                if attempt + 1 < self._attempts:
                    await self._sleep_fn(connect_backoff_seconds(attempt))
                continue
            self._context = context
            return response
        raise AnthropicProxyError(
            503,
            f"{self._label} upstream unreachable after {self._attempts} connect attempts: {last_error}",
            code="upstream_unreachable",
        ) from last_error

    async def __aexit__(self, exc_type, exc, tb) -> bool | None:
        if self._context is None:
            return None
        return await self._context.__aexit__(exc_type, exc, tb)
