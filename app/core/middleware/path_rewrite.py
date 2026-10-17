from __future__ import annotations

from typing import Any

from fastapi import FastAPI

_CODEX_V1_PREFIX = "/backend-api/codex/v1/"
_CODEX_V1_PREFIX_BYTES = _CODEX_V1_PREFIX.encode("ascii")
_CODEX_CANONICAL_PREFIX = "/backend-api/codex/"
_CODEX_CANONICAL_PREFIX_BYTES = _CODEX_CANONICAL_PREFIX.encode("ascii")


_CODEX_IMAGE_ALIASES = {
    "/backend-api/codex/images/generations": "/v1/images/generations",
    "/backend-api/codex/images/edits": "/v1/images/edits",
}


def _canonicalize_backend_api_codex_path(path: str) -> str:

    if not path.startswith(_CODEX_V1_PREFIX):
        return path
    return _CODEX_CANONICAL_PREFIX + path[len(_CODEX_V1_PREFIX) :]


def _canonicalize_raw_path(raw_path: bytes) -> bytes:
    if not raw_path.startswith(_CODEX_V1_PREFIX_BYTES):
        return raw_path
    return _CODEX_CANONICAL_PREFIX_BYTES + raw_path[len(_CODEX_V1_PREFIX_BYTES) :]


class BackendApiCodexV1AliasMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
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
