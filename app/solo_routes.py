from __future__ import annotations

from collections.abc import Iterable

from fastapi import APIRouter, FastAPI
from starlette.routing import BaseRoute

KEPT_HEALTH_PATHS = frozenset({"/health/live", "/health/ready"})


def _key(route: BaseRoute) -> tuple[str, frozenset[str]] | None:
    path = getattr(route, "path", None)
    if not isinstance(path, str):
        return None
    methods = getattr(route, "methods", None) or ()
    return path, frozenset(methods)


def _proxy_routers() -> Iterable[APIRouter]:
    from app.modules.proxy import api as proxy_api
    from app.modules.proxy.models_api import router as models

    return (
        proxy_api.router,
        proxy_api.internal_router,
        proxy_api.ws_router,
        proxy_api.wham_router,
        proxy_api.frame_db_router,
        proxy_api.v1_router,
        proxy_api.v1_ws_router,
        proxy_api.transcribe_router,
        proxy_api.files_router,
        proxy_api.usage_router,
        models,
    )


def kept_keys() -> frozenset[tuple[str, frozenset[str]]]:
    keys = {key for router in _proxy_routers() for key in map(_key, router.routes) if key is not None}
    return frozenset(keys)


def keep_proxy_only(app: FastAPI) -> int:
    kept = kept_keys()
    before = len(app.router.routes)
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if (key := _key(route)) is not None and (key in kept or key[0] in KEPT_HEALTH_PATHS)
    ]
    app.openapi_url = None
    app.docs_url = None
    app.redoc_url = None
    return before - len(app.router.routes)
