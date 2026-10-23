from __future__ import annotations

from importlib import import_module
from types import ModuleType

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_DRAIN_ALLOWED_HTTP_PATHS = frozenset(
    {
        "/health/live",
        "/internal/drain/start",
        "/internal/drain/stop",
        "/internal/drain/status",
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
        "/internal/oauth/pending",
    }
)
_IN_FLIGHT_WEBSOCKET_PATHS = frozenset({"/backend-api/codex/responses", "/v1/responses"})


class InFlightMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._shutdown_state: ModuleType | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        shutdown_state = self._shutdown_state
        if shutdown_state is None:
            shutdown_state = self._shutdown_state = import_module("app.core.shutdown")
        if scope_type == "websocket":
            shutdown_state.increment_in_flight()
            if shutdown_state.is_draining():
                shutdown_state.decrement_in_flight()
                await send({"type": "websocket.close", "code": 1013, "reason": "Server is draining"})
                return
            if scope.get("path", "") not in _IN_FLIGHT_WEBSOCKET_PATHS:
                shutdown_state.decrement_in_flight()
                await self.app(scope, receive, send)
                return
            try:
                await self.app(scope, receive, send)
            finally:
                shutdown_state.decrement_in_flight()
            return
        path = scope.get("path", "")
        track_in_flight = path not in _IN_FLIGHT_EXCLUDED_HTTP_PATHS
        if track_in_flight:
            shutdown_state.increment_in_flight()
        try:
            if shutdown_state.is_draining() and path not in _DRAIN_ALLOWED_HTTP_PATHS:
                response = JSONResponse(
                    status_code=503, content={"error": {"type": "service_unavailable", "message": "Server is draining"}}
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            if track_in_flight:
                shutdown_state.decrement_in_flight()
