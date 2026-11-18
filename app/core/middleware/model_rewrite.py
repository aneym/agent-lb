from __future__ import annotations

from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MODEL_REWRITTEN_HEADER = "x-agent-lb-model-rewritten"

_REWRITES: ContextVar[list[str] | None] = ContextVar("model_rewrites", default=None)


def note_model_rewrite(requested: str | None, enforced: str) -> None:
    rewrites = _REWRITES.get()
    if rewrites is None:
        return
    value = f"{requested or '-'}->{enforced}"
    rewrites.append(value.encode("latin-1", "replace").decode("latin-1"))


class ModelRewriteMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rewrites: list[str] = []
        token = _REWRITES.set(rewrites)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start" and rewrites:
                MutableHeaders(scope=message)[MODEL_REWRITTEN_HEADER] = rewrites[-1]
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _REWRITES.reset(token)
