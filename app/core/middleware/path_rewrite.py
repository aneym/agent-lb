from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI

from app.core.clients.proxy_websocket import REALTIME_LIVE_CALL_ID_ROUTE_REGEX

_CODEX_V1_PREFIX = "/backend-api/codex/v1/"
_CODEX_V1_PREFIX_BYTES = _CODEX_V1_PREFIX.encode("ascii")
_CODEX_CANONICAL_PREFIX = "/backend-api/codex/"
_CODEX_CANONICAL_PREFIX_BYTES = _CODEX_CANONICAL_PREFIX.encode("ascii")
_V1_LIVE_PREFIX = "/v1/live/"
_V1_LIVE_PREFIX_BYTES = _V1_LIVE_PREFIX.encode("ascii")
_V1_REALTIME_PATH = "/v1/realtime"
_V1_REALTIME_RAW_PATH = _V1_REALTIME_PATH.encode("ascii")
_REDACTED_CALL_ID = "<redacted>"
_REDACTED_CALL_ID_BYTES = b"%3Credacted%3E"
_REALTIME_LIVE_CALL_ID_PATTERN = re.compile(f"{REALTIME_LIVE_CALL_ID_ROUTE_REGEX}\\Z")
_REALTIME_LIVE_PATH_REDACTIONS = (
    (_CODEX_V1_PREFIX, f"{_CODEX_V1_PREFIX}{_REDACTED_CALL_ID}", _CODEX_V1_PREFIX_BYTES + _REDACTED_CALL_ID_BYTES),
    (
        _CODEX_CANONICAL_PREFIX,
        f"{_CODEX_CANONICAL_PREFIX}{_REDACTED_CALL_ID}",
        _CODEX_CANONICAL_PREFIX_BYTES + _REDACTED_CALL_ID_BYTES,
    ),
)
_CODEX_IMAGE_ALIASES = {
    "/backend-api/codex/images/generations": "/v1/images/generations",
    "/backend-api/codex/images/edits": "/v1/images/edits",
}


def _canonicalize_backend_api_codex_path(path: str) -> str:
    if not path.startswith(_CODEX_V1_PREFIX):
        return path
    return _CODEX_CANONICAL_PREFIX + path[len(_CODEX_V1_PREFIX) :]


def _realtime_live_scope_redaction(path: str) -> tuple[str, bytes] | None:
    if path == _V1_REALTIME_PATH:
        return (_V1_REALTIME_PATH, _V1_REALTIME_RAW_PATH)
    if path.startswith(_V1_LIVE_PREFIX):
        return (f"{_V1_LIVE_PREFIX}{_REDACTED_CALL_ID}", _V1_LIVE_PREFIX_BYTES + _REDACTED_CALL_ID_BYTES)
    for prefix, redacted_path, redacted_raw_path in _REALTIME_LIVE_PATH_REDACTIONS:
        if path.startswith(prefix) and _REALTIME_LIVE_CALL_ID_PATTERN.fullmatch(path[len(prefix) :]) is not None:
            return (redacted_path, redacted_raw_path)
    return None


def redact_realtime_live_path(path: str) -> str:
    redaction = _realtime_live_scope_redaction(path)
    return path if redaction is None else redaction[0]


def _canonicalize_raw_path(raw_path: bytes) -> bytes:
    if not raw_path.startswith(_CODEX_V1_PREFIX_BYTES):
        return raw_path
    return _CODEX_CANONICAL_PREFIX_BYTES + raw_path[len(_CODEX_V1_PREFIX_BYTES) :]


class BackendApiCodexV1AliasMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path")
        if scope.get("type") == "websocket" and isinstance(path, str):
            redaction = _realtime_live_scope_redaction(path)
            if redaction is not None:
                routing_scope = dict(scope)
                routing_scope["path"] = _canonicalize_backend_api_codex_path(path)
                if isinstance(raw_path := routing_scope.get("raw_path"), bytes):
                    routing_scope["raw_path"] = _canonicalize_raw_path(raw_path)
                scope["path"], scope["raw_path"] = redaction
                scope["query_string"] = b""
                await self.app(routing_scope, receive, send)
                return
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path")
            if isinstance(path, str):
                rewritten = _canonicalize_backend_api_codex_path(path)
                if scope.get("type") == "http":
                    rewritten = _CODEX_IMAGE_ALIASES.get(rewritten, rewritten)
                if rewritten != path:
                    scope = dict(scope)
                    scope["path"] = rewritten
                    raw_path = scope.get("raw_path")
                    if isinstance(raw_path, bytes):
                        canonical_raw = _canonicalize_raw_path(raw_path)
                        if (
                            scope.get("type") == "http"
                            and canonical_raw.decode("ascii", errors="replace") in _CODEX_IMAGE_ALIASES
                        ):
                            canonical_raw = rewritten.encode("ascii")
                        scope["raw_path"] = canonical_raw
        await self.app(scope, receive, send)


def add_backend_api_codex_v1_alias_middleware(app: FastAPI) -> None:
    app.add_middleware(BackendApiCodexV1AliasMiddleware)
