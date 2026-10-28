from __future__ import annotations

from importlib import import_module

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

_DRAIN_ALLOWED_HTTP_PATHS = frozenset(
    {
        "/health/live",
        "/internal/drain/start",
        "/internal/drain/stop",
        "/internal/drain/status",
        "/internal/drain/close-websockets",
        "/internal/oauth/pending",
        "/internal/bridge/responses",
    }
)

_IN_FLIGHT_EXCLUDED_HTTP_PATHS = frozenset(
    {
        "/health/live",
        "/health/ready",
        "/health/startup",
        "/internal/drain/start",
        "/internal/drain/stop",
        "/internal/drain/status",
        "/internal/drain/close-websockets",
        "/internal/oauth/pending",
    }
)


class InFlightMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:

        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        shutdown_state = import_module("app.core.shutdown")

        path = scope.get("path", "")
        if not shutdown_state.admits_requests() and path not in _DRAIN_ALLOWED_HTTP_PATHS:
            await _draining_response()(scope, receive, send)
            return

        if path in _IN_FLIGHT_EXCLUDED_HTTP_PATHS:
            await self.app(scope, receive, send)
            return

        shutdown_state.increment_in_flight()
        try:
            await self.app(scope, receive, send)
        finally:
            shutdown_state.decrement_in_flight()

    async def _websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        shutdown_state = import_module("app.core.shutdown")
        if not shutdown_state.admits_requests():
            websocket = WebSocket(scope, receive, send)
            if "websocket.http.response" in scope.get("extensions", {}):
                await websocket.send_denial_response(_draining_response())
            else:
                await websocket.close(code=1013, reason="Server is draining")
            return
        shutdown_state.increment_websockets()
        try:
            await self.app(scope, receive, send)
        finally:
            shutdown_state.decrement_websockets()


def _draining_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "type": "service_unavailable",
                "message": "Server is draining",
            }
        },
    )
