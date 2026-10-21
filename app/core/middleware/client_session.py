from __future__ import annotations

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.auth.dependencies import validate_eval_api_access
from app.core.context_parts.capture import context_parts_state, wrap_receive
from app.core.eval_guard import EvalGuardState, eval_guard_state, is_eval_proxy_path
from app.core.exceptions import DashboardForbiddenError, ProxyAuthError
from app.core.utils.client_session import (
    reset_attribution,
    reset_caller_seat,
    reset_client_session_id,
    reset_dispatch_id,
    reset_room,
    set_attribution_from_scope,
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
        guard = eval_guard_state.set(EvalGuardState())
        token = set_client_session_id_from_scope(scope)
        dispatch = set_dispatch_id_from_scope(scope)
        seat = set_caller_seat_from_scope(scope)
        room = set_room_from_scope(scope)
        attribution = set_attribution_from_scope(scope)
        receive, parts_token = wrap_receive(scope, receive)
        try:
            if not is_eval_proxy_path(scope["path"]):
                try:
                    await validate_eval_api_access(HTTPConnection(scope))
                except (DashboardForbiddenError, ProxyAuthError) as exc:
                    if scope["type"] == "websocket":
                        await send({"type": "websocket.close", "code": 1008})
                    else:
                        await JSONResponse(
                            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code
                        )(scope, receive, send)
                    return
            await self.app(scope, receive, send)
        finally:
            if parts_token is not None:
                context_parts_state.reset(parts_token)
            eval_guard_state.reset(guard)
            reset_attribution(attribution)
            reset_room(room)
            reset_caller_seat(seat)
            reset_dispatch_id(dispatch)
            reset_client_session_id(token)
