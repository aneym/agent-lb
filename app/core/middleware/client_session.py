from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.utils.client_session import reset_client_session_id, set_client_session_id_from_scope


class ClientSessionMiddleware:
    """Expose the client's own session id (see app.core.utils.client_session) to request logging."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        token = set_client_session_id_from_scope(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_client_session_id(token)
