from __future__ import annotations

import pytest

from app.core.middleware.path_rewrite import (
    BackendApiCodexV1AliasMiddleware,
    _canonicalize_backend_api_codex_path,
    _canonicalize_raw_path,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [

        ("/backend-api/codex/v1/models", "/backend-api/codex/models"),
        ("/backend-api/codex/v1/responses", "/backend-api/codex/responses"),
        (
            "/backend-api/codex/v1/responses/compact",
            "/backend-api/codex/responses/compact",
        ),

        ("/backend-api/codex/models", "/backend-api/codex/models"),
        ("/backend-api/codex/responses", "/backend-api/codex/responses"),



        ("/backend-api/codex", "/backend-api/codex"),
        ("/backend-api/codex/v1", "/backend-api/codex/v1"),


        ("/v1/models", "/v1/models"),
        ("/v1/responses", "/v1/responses"),

        ("/api/settings", "/api/settings"),
        ("/", "/"),
    ],
)
def test_canonicalize_backend_api_codex_path(raw: str, expected: str) -> None:
    assert _canonicalize_backend_api_codex_path(raw) == expected


def test_canonicalize_backend_api_codex_path_is_idempotent() -> None:
    once = _canonicalize_backend_api_codex_path("/backend-api/codex/v1/responses")
    twice = _canonicalize_backend_api_codex_path(once)
    assert once == twice == "/backend-api/codex/responses"


def test_canonicalize_raw_path_preserves_query_segment() -> None:



    raw = b"/backend-api/codex/v1/models"
    assert _canonicalize_raw_path(raw) == b"/backend-api/codex/models"


def test_canonicalize_raw_path_noop_for_canonical() -> None:
    raw = b"/backend-api/codex/models"
    assert _canonicalize_raw_path(raw) is raw or _canonicalize_raw_path(raw) == raw








class _RecordingApp:


    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, scope: dict, receive, send) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(scope)


@pytest.mark.asyncio
async def test_middleware_rewrites_http_scope() -> None:
    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    scope = {
        "type": "http",
        "path": "/backend-api/codex/v1/responses",
        "raw_path": b"/backend-api/codex/v1/responses",
    }

    async def _receive():
        return {"type": "http.request"}

    async def _send(message):
        pass

    await middleware(scope, _receive, _send)

    assert len(inner.calls) == 1
    seen = inner.calls[0]
    assert seen["path"] == "/backend-api/codex/responses"
    assert seen["raw_path"] == b"/backend-api/codex/responses"


@pytest.mark.asyncio
async def test_middleware_rewrites_websocket_scope() -> None:

    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    scope = {
        "type": "websocket",
        "path": "/backend-api/codex/v1/responses",
        "raw_path": b"/backend-api/codex/v1/responses",
    }

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(scope, _receive, _send)

    assert len(inner.calls) == 1
    seen = inner.calls[0]
    assert seen["path"] == "/backend-api/codex/responses"
    assert seen["raw_path"] == b"/backend-api/codex/responses"


@pytest.mark.asyncio
async def test_middleware_leaves_lifespan_scope_untouched() -> None:

    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    scope = {"type": "lifespan"}

    async def _receive():
        return {"type": "lifespan.startup"}

    async def _send(message):
        pass

    await middleware(scope, _receive, _send)

    assert len(inner.calls) == 1
    assert inner.calls[0] is scope


@pytest.mark.asyncio
async def test_middleware_does_not_mutate_caller_scope_on_rewrite() -> None:

    inner = _RecordingApp()
    middleware = BackendApiCodexV1AliasMiddleware(inner)

    original_scope = {
        "type": "websocket",
        "path": "/backend-api/codex/v1/responses",
        "raw_path": b"/backend-api/codex/v1/responses",
    }
    snapshot = dict(original_scope)

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        pass

    await middleware(original_scope, _receive, _send)

    assert original_scope == snapshot
    assert inner.calls[0]["path"] == "/backend-api/codex/responses"
