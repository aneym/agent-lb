from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.core import upload_throttle
from app.core.config.settings import get_settings
from app.core.forensics import register_stack_dump_signal
from app.core.handlers import add_exception_handlers
from app.core.metrics.middleware import MetricsMiddleware
from app.core.middleware import (
    add_api_firewall_middleware,
    add_app_version_middleware,
    add_backend_api_codex_v1_alias_middleware,
    add_dashboard_auth_proxy_middleware,
    add_request_decompression_middleware,
    add_request_id_middleware,
)
from app.core.middleware.client_session import ClientSessionMiddleware
from app.core.middleware.identity import IdentityMiddleware
from app.core.middleware.inflight import InFlightMiddleware
from app.core.resilience.backpressure import BackpressureMiddleware
from app.core.resilience.bulkhead import BulkheadMiddleware, get_bulkhead
from app.core.resilience.memory_monitor import configure as configure_memory_monitor
from app.lifecycle import lifespan
from app.modules.account_schedule import api as account_schedule_api
from app.modules.accounts import api as accounts_api
from app.modules.accounts.availability_api import availability_router
from app.modules.api_keys import api as api_keys_api
from app.modules.audit import api as audit_api
from app.modules.conversation_archive import api as conversation_archive_api
from app.modules.dashboard import api as dashboard_api
from app.modules.dashboard_auth import api as dashboard_auth_api
from app.modules.federation import api as federation_api
from app.modules.firewall import api as firewall_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.pools import api as pools_api
from app.modules.proxy import api as proxy_api
from app.modules.proxy import meta_service as meta_api
from app.modules.public_usage import api as public_usage_api
from app.modules.quota_planner import api as quota_planner_api
from app.modules.receipts import api as receipts_api
from app.modules.reports import api as reports_api
from app.modules.request_logs import api as request_logs_api
from app.modules.routing_policy import api as routing_policy_api
from app.modules.routing_view import api as routing_view_api
from app.modules.runtime import api as runtime_api
from app.modules.sessions import api as sessions_api
from app.modules.settings import api as settings_api
from app.modules.sticky_sessions import api as sticky_sessions_api
from app.modules.team import api as team_api
from app.modules.usage import api as usage_api


def _resolve_static_asset_path(static_root: Path, requested_path: str) -> Path | None:

    normalized = PurePosixPath(requested_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        return None
    full_path, stat_result = StaticFiles(directory=static_root, check_dir=False).lookup_path(normalized.as_posix())
    if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
        return None
    return Path(full_path)


def create_app() -> FastAPI:

    upload_throttle.install()
    settings = get_settings()
    register_stack_dump_signal()
    configure_memory_monitor(
        warning_threshold_mb=settings.memory_warning_threshold_mb,
        reject_threshold_mb=settings.memory_reject_threshold_mb,
    )
    app = FastAPI(
        title="agent-lb",
        version="0.1.0",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    app.add_middleware(cast(Any, InFlightMiddleware))
    app.add_middleware(cast(Any, ClientSessionMiddleware))
    app.add_middleware(cast(Any, IdentityMiddleware))
    add_dashboard_auth_proxy_middleware(app)
    add_request_decompression_middleware(app)
    add_request_id_middleware(app)
    add_api_firewall_middleware(app)
    app.add_middleware(cast(Any, MetricsMiddleware), enabled=settings.metrics_enabled)
    if settings.backpressure_max_concurrent_requests > 0:
        app.add_middleware(
            cast(Any, BackpressureMiddleware),
            max_concurrent=settings.backpressure_max_concurrent_requests,
        )
    proxy_http_limit = settings.bulkhead_proxy_http_limit
    proxy_websocket_limit = settings.bulkhead_proxy_websocket_limit
    proxy_compact_limit = settings.bulkhead_proxy_compact_limit
    assert proxy_http_limit is not None
    assert proxy_websocket_limit is not None
    assert proxy_compact_limit is not None
    app.add_middleware(
        cast(Any, BulkheadMiddleware),
        bulkhead=get_bulkhead(
            proxy_http_limit=proxy_http_limit,
            proxy_websocket_limit=proxy_websocket_limit,
            proxy_compact_limit=proxy_compact_limit,
            dashboard_limit=settings.bulkhead_dashboard_limit,
        ),
    )
    add_backend_api_codex_v1_alias_middleware(app)
    add_app_version_middleware(app)
    add_exception_handlers(app)

    for router in (
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
    ):
        app.include_router(router)
    app.include_router(meta_api.router)
    app.include_router(meta_api.provider_router)
    app.include_router(audit_api.router)
    app.include_router(accounts_api.router)
    app.include_router(availability_router)
    app.include_router(account_schedule_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(usage_api.router)
    app.include_router(public_usage_api.router)
    app.include_router(request_logs_api.router)
    app.include_router(receipts_api.router)
    app.include_router(routing_view_api.router)
    app.include_router(routing_policy_api.router)
    app.include_router(sessions_api.router)
    app.include_router(sessions_api.short_link_router)
    app.include_router(pools_api.router)
    app.include_router(quota_planner_api.router)
    app.include_router(reports_api.router)
    app.include_router(conversation_archive_api.router)
    app.include_router(runtime_api.router)
    app.include_router(oauth_api.router)
    app.include_router(dashboard_auth_api.router)
    app.include_router(settings_api.router)
    app.include_router(firewall_api.router)
    app.include_router(sticky_sessions_api.router)
    app.include_router(api_keys_api.router)
    app.include_router(team_api.router)
    app.include_router(health_api.router)
    app.include_router(federation_api.router)
    app.include_router(federation_api.dashboard_router)

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"
    static_root = static_dir.resolve()
    frontend_build_hint = "Frontend assets are missing. Run `cd frontend && bun run build`."
    excluded_prefixes = ("api/", "v1/", "backend-api/", "health")

    def _is_static_asset_path(path: str) -> bool:
        if path.startswith("assets/"):
            return True
        last_segment = path.rsplit("/", maxsplit=1)[-1]
        return "." in last_segment

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str = ""):
        normalized = path.lstrip("/")
        if normalized and any(
            normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in excluded_prefixes
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        if normalized:
            candidate = _resolve_static_asset_path(static_root, normalized)
            if candidate is not None:
                return FileResponse(candidate)
            if _is_static_asset_path(normalized):
                raise HTTPException(status_code=404, detail="Not Found")

        if not index_html.is_file():
            raise HTTPException(status_code=503, detail=frontend_build_hint)

        return FileResponse(
            index_html,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    return app


app = create_app()
