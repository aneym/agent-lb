from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.identity import reset_request_identity, resolve_scope_identity, set_request_identity


class IdentityMiddleware:
    """Put the caller's machine (and keyless user) in the request context for request logging.

    Resolved once per HTTP request and once per websocket connection. The request-log
    writer adds the member from the validated key (see app.core.identity).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        identity = resolve_scope_identity(Headers(scope=scope), client[0] if client else None)
        token = set_request_identity(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_identity(token)
