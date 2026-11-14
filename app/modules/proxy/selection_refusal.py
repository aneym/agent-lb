from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError

Recorder = Callable[[AnthropicProxyError], Awaitable[None]]


class SelectionRefusal(AnthropicProxyError):
    pass


def recorder(persist: Callable[..., Awaitable[None]], **fields: Any) -> Recorder:
    async def record(exc: AnthropicProxyError) -> None:
        if isinstance(exc, SelectionRefusal):
            await persist(account=None, status="error", error_code=exc.code, error_message=exc.message, **fields)

    return record


async def watch(body: AsyncIterator[bytes], record: Recorder) -> AsyncIterator[bytes]:
    try:
        async for chunk in body:
            yield chunk
    except AnthropicProxyError as exc:
        await record(exc)
        raise
    finally:
        aclose = getattr(body, "aclose", None)
        if aclose is not None:
            await aclose()
