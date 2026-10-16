from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.utils.client_session import (
    reset_caller_seat,
    reset_client_session_id,
    reset_dispatch_id,
    reset_room,
    set_caller_seat_from_scope,
    set_client_session_id_from_scope,
    set_dispatch_id_from_scope,
    set_room_from_scope,
)


class ClientSessionMiddleware:


    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        token = set_client_session_id_from_scope(scope)
        dispatch = set_dispatch_id_from_scope(scope)
        seat = set_caller_seat_from_scope(scope)
        room = set_room_from_scope(scope)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_room(room)
            reset_caller_seat(seat)
            reset_dispatch_id(dispatch)
            reset_client_session_id(token)
