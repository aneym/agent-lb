from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI

from app.core.metrics.middleware import MetricsMiddleware
from app.core.middleware.api_firewall import add_api_firewall_middleware
from app.core.middleware.app_version import add_app_version_middleware
from app.core.middleware.arrival_timing import add_arrival_timing_middleware
from app.core.middleware.client_session import ClientSessionMiddleware
from app.core.middleware.dashboard_auth_proxy import add_dashboard_auth_proxy_middleware
from app.core.middleware.identity import IdentityMiddleware
from app.core.middleware.inflight import InFlightMiddleware
from app.core.middleware.model_rewrite import ModelRewriteMiddleware
from app.core.middleware.path_rewrite import add_backend_api_codex_v1_alias_middleware
from app.core.middleware.request_decompression import add_request_decompression_middleware
from app.core.middleware.request_id import add_request_id_middleware
from app.core.model_aliases import Aliases
from app.core.resilience.backpressure import BackpressureMiddleware
from app.core.resilience.bulkhead import BulkheadMiddleware, get_bulkhead


def add_middleware_stack(app: FastAPI, settings) -> None:
    app.add_middleware(cast(Any, InFlightMiddleware))
    app.add_middleware(cast(Any, ClientSessionMiddleware))
    app.add_middleware(cast(Any, IdentityMiddleware))
    add_dashboard_auth_proxy_middleware(app)
    app.add_middleware(Aliases)
    add_request_decompression_middleware(app)
    add_request_id_middleware(app)
    app.add_middleware(cast(Any, ModelRewriteMiddleware))
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
    add_arrival_timing_middleware(app)
