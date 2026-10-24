from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from importlib import import_module
from typing import Any, cast

from fastapi import FastAPI

from app.bridge_startup import ensure_bridge_durable_schema_ready
from app.core import upload_throttle
from app.core.auth.guardian import build_auth_guardian_scheduler
from app.core.bootstrap import ensure_auto_bootstrap_token, log_bootstrap_token
from app.core.clients.codex_pool import close_codex_pool
from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.forensics import register_stack_dump_signal
from app.core.handlers import add_exception_handlers
from app.core.identity_nodes import IdentityNodeCache
from app.core.metrics.middleware import MetricsMiddleware
from app.core.metrics.prometheus import mark_process_dead
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
from app.core.model_aliases import Aliases
from app.core.openai.model_refresh_scheduler import build_model_refresh_scheduler
from app.core.resilience.backpressure import BackpressureMiddleware
from app.core.resilience.bulkhead import BulkheadMiddleware, get_bulkhead
from app.core.resilience.event_loop_lag_monitor import build_event_loop_lag_monitor
from app.core.resilience.memory_monitor import configure as configure_memory_monitor
from app.core.usage.refresh_scheduler import build_usage_refresh_scheduler
from app.db.session import SessionLocal, close_db, init_background_db, init_db
from app.metrics_startup import start_metrics_server
from app.muse_routes import register_muse_routes
from app.modules.account_schedule import api as account_schedule_api
from app.modules.account_schedule.scheduler import build_account_resume_scheduler
from app.modules.accounts import api as accounts_api
from app.modules.accounts.availability_api import availability_router
from app.modules.accounts.cache_warmer import build_accounts_cache_warmer
from app.modules.accounts.pulse import build_account_pulse_scheduler
from app.modules.accounts.reset_credit_scheduler import build_reset_credit_auto_redeem_scheduler
from app.modules.api_keys import api as api_keys_api
from app.modules.api_keys.reset_scheduler import build_api_key_limit_reset_scheduler
from app.modules.audit import api as audit_api
from app.modules.conversation_archive import api as conversation_archive_api
from app.modules.dashboard import api as dashboard_api
from app.modules.dashboard_auth import api as dashboard_auth_api
from app.modules.federation import api as federation_api
from app.modules.federation.push import build_federation_push_scheduler
from app.modules.federation.scheduler import build_federation_mirror_scheduler
from app.modules.firewall import api as firewall_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.pools import api as pools_api
from app.modules.pools.seat_quota_scheduler import build_seat_quota_scheduler
from app.modules.proxy import api as proxy_api
from app.modules.proxy.models_api import router as models
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.ring_membership import (
    RING_HEARTBEAT_INTERVAL_SECONDS,
    RING_STALE_GRACE_SECONDS,
    RING_STALE_THRESHOLD_SECONDS,
    RingMembershipService,
)
from app.modules.public_usage import api as public_usage_api
from app.modules.quota_planner import api as quota_planner_api
from app.modules.quota_planner.scheduler import build_quota_planner_scheduler
from app.modules.receipts import api as receipts_api
from app.modules.reports import api as reports_api
from app.modules.request_logs import api as request_logs_api
from app.modules.routing_policy import api as routing_policy_api
from app.modules.routing_view import api as routing_view_api
from app.modules.runtime import api as runtime_api
from app.modules.sessions import api as sessions_api
from app.modules.settings import api as settings_api
from app.modules.sticky_sessions import api as sticky_sessions_api
from app.modules.sticky_sessions.cleanup_scheduler import build_sticky_session_cleanup_scheduler
from app.modules.team import api as team_api
from app.modules.usage import api as usage_api
from app.modules.usage.additional_quota_keys import reload_additional_quota_registry
from app.runtime_diagnostics import log_runtime_diagnostic_fingerprint
from app.static_routes import configure_static_routes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.core.startup as startup_module
    from app.bridge_startup import wait_for_bridge_advertise_endpoint

    startup_recorder = startup_module.StartupRecorder()
    shutdown_state = import_module("app.core.shutdown")
    metrics_server = None
    metrics_server_task: asyncio.Task[None] | None = None
    ring_service = None
    heartbeat_task: asyncio.Task[None] | None = None
    instance_id = None

    startup_module.startup_complete = False
    startup_module.reset_bridge_registration()
    shutdown_state.reset()
    async with startup_recorder.phase("settings_and_caches"):
        await get_settings_cache().invalidate()
        await get_rate_limit_headers_cache().invalidate()
        reload_additional_quota_registry()
        settings = get_settings()
    node_cache = IdentityNodeCache(settings)
    bridge_endpoint_base_url = settings.http_responses_session_bridge_advertise_base_url
    if settings.otel_enabled:
        from app.core.tracing.otel import init_tracing

        init_tracing(service_name="agent-lb", endpoint=settings.otel_exporter_endpoint, app=app)
    async with startup_recorder.phase("database"):
        await init_db()
        init_background_db()
    async with startup_recorder.phase("bootstrap_token"):
        _auto_bootstrap_token = await ensure_auto_bootstrap_token()
    if _auto_bootstrap_token:
        log_bootstrap_token(logger, _auto_bootstrap_token)
    async with startup_recorder.phase("http_client"):
        await init_http_client()
    async with startup_recorder.phase("bridge_schema"):
        bridge_durable_schema_ready = await ensure_bridge_durable_schema_ready(settings)
    log_runtime_diagnostic_fingerprint(settings, bridge_durable_schema_ready=bridge_durable_schema_ready)
    if bridge_durable_schema_ready:
        startup_module.mark_bridge_durable_schema_ready()
    async with startup_recorder.phase("schedulers"):
        usage_scheduler = build_usage_refresh_scheduler()
        seat_quota_scheduler = build_seat_quota_scheduler()
        api_key_limit_reset_scheduler = build_api_key_limit_reset_scheduler()
        model_scheduler = build_model_refresh_scheduler()
        sticky_session_cleanup_scheduler = build_sticky_session_cleanup_scheduler()
        quota_planner_scheduler = build_quota_planner_scheduler()
        auth_guardian_scheduler = build_auth_guardian_scheduler()
        account_pulse_scheduler = build_account_pulse_scheduler()
        account_resume_scheduler = build_account_resume_scheduler()
        reset_credit_auto_redeem_scheduler = build_reset_credit_auto_redeem_scheduler()
        if settings.federation_token and not settings.federation_transfer_inbound_sha256:
            logger.warning("Federation transfer routes are disabled until a transfer token is set")
        federation_mirror_scheduler = build_federation_mirror_scheduler()
        app.state.federation_mirror_scheduler = federation_mirror_scheduler
        federation_push_scheduler = build_federation_push_scheduler()
        app.state.federation_push_scheduler = federation_push_scheduler
        event_loop_lag_monitor = build_event_loop_lag_monitor(
            warning_threshold_seconds=settings.event_loop_lag_warning_threshold_seconds
        )
        await usage_scheduler.start()
        await seat_quota_scheduler.start()
        await api_key_limit_reset_scheduler.start()
        await model_scheduler.start()
        await sticky_session_cleanup_scheduler.start()
        await quota_planner_scheduler.start()
        await auth_guardian_scheduler.start()
        await account_pulse_scheduler.start()
        await account_resume_scheduler.start()
        await reset_credit_auto_redeem_scheduler.start()
        await federation_mirror_scheduler.start()
        await federation_push_scheduler.start()
        await event_loop_lag_monitor.start()
    metrics_server, metrics_server_task = start_metrics_server(settings)

    async def _complete_bridge_registration(svc: RingMembershipService, iid: str) -> None:
        from app.bridge_startup import validate_bridge_advertise_endpoint_for_multi_replica

        if bridge_endpoint_base_url is None:
            await _activate_bridge_membership(svc, iid)
            startup_module.mark_bridge_registration_complete()
            return
        await validate_bridge_advertise_endpoint_for_multi_replica(
            svc=svc,
            settings=settings,
            instance_id=iid,
            endpoint_base_url=bridge_endpoint_base_url,
        )
        await svc.register(iid, endpoint_base_url=None)
        await wait_for_bridge_advertise_endpoint(
            bridge_endpoint_base_url,
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
        )
        await svc.heartbeat(iid, endpoint_base_url=bridge_endpoint_base_url)
        startup_module.mark_bridge_registration_complete()

    async def _heartbeat_only(svc: RingMembershipService, iid: str) -> None:
        while True:
            await asyncio.sleep(RING_HEARTBEAT_INTERVAL_SECONDS)
            try:
                await svc.heartbeat(iid, endpoint_base_url=bridge_endpoint_base_url)
            except Exception:
                logger.warning("Ring heartbeat failed", exc_info=True)

    async def _register_and_heartbeat(svc: RingMembershipService, iid: str) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                await _complete_bridge_registration(svc, iid)
                startup_recorder.ready()
                logger.info("Registered in bridge ring", extra={"instance_id": iid, "attempt": attempt})
                break
            except Exception:
                delay = min(5.0 * (2 ** min(attempt - 1, 5)), 60.0)
                logger.warning("Ring attempt %d failed; retry in %.0fs", attempt, delay, exc_info=True)
                await asyncio.sleep(delay)
        await _heartbeat_only(svc, iid)

    async def _activate_bridge_membership(svc: RingMembershipService, iid: str) -> None:
        if bridge_endpoint_base_url is None:
            await svc.register(iid, endpoint_base_url=None)
            return
        await svc.register(iid, endpoint_base_url=bridge_endpoint_base_url)

    from app.core.auth.api_key_cache import get_api_key_cache
    from app.core.cache.invalidation import (
        NAMESPACE_API_KEY,
        NAMESPACE_FIREWALL,
        CacheInvalidationPoller,
        set_cache_invalidation_poller,
    )
    from app.core.middleware.firewall_cache import get_firewall_ip_cache

    async with startup_recorder.phase("cache_poller"):
        cache_poller = CacheInvalidationPoller(SessionLocal)
        cache_poller.on_invalidation(NAMESPACE_API_KEY, get_api_key_cache().clear)
        cache_poller.on_invalidation(NAMESPACE_FIREWALL, get_firewall_ip_cache().invalidate_all)
        set_cache_invalidation_poller(cache_poller)
        await cache_poller.start()

    async with startup_recorder.phase("accounts_cache_warm"):
        accounts_cache_warmer = build_accounts_cache_warmer()
        await accounts_cache_warmer.start()

    ring_service: RingMembershipService | None = None
    instance_id: str | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    ring_service = RingMembershipService(SessionLocal)
    instance_id = settings.http_responses_session_bridge_instance_id
    heartbeat_task = asyncio.create_task(_register_and_heartbeat(ring_service, instance_id))
    await node_cache.start()
    startup_module.startup_complete = True
    startup_recorder.complete("ok")

    try:
        yield
    finally:
        shutdown_state.set_bridge_drain_active(True)
        shutdown_state.set_draining(True)
        drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=settings.shutdown_drain_timeout_seconds)
        if not drained:
            logger.warning("Drain timeout reached, proceeding with shutdown")

        proxy_service = getattr(app.state, "proxy_service", None)
        if proxy_service is not None and hasattr(proxy_service, "mark_http_bridge_draining"):
            try:
                await proxy_service.mark_http_bridge_draining()
            except Exception:
                logger.warning("HTTP bridge drain failed", exc_info=True)
        if proxy_service is not None and hasattr(proxy_service, "close_all_http_bridge_sessions"):
            try:
                await proxy_service.close_all_http_bridge_sessions()
            except Exception:
                logger.warning("HTTP bridge close failed", exc_info=True)

        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await asyncio.wait_for(heartbeat_task, timeout=2)
            except (asyncio.CancelledError, TimeoutError):
                pass

        if ring_service is not None and instance_id is not None:
            try:
                await asyncio.wait_for(
                    ring_service.mark_stale(
                        instance_id,
                        stale_threshold_seconds=RING_STALE_THRESHOLD_SECONDS,
                        grace_seconds=RING_STALE_GRACE_SECONDS,
                    ),
                    timeout=3,
                )
                logger.info(
                    "Marked bridge ring membership stale for shutdown",
                    extra={"instance_id": instance_id},
                )
            except Exception:
                logger.warning("Failed to mark bridge ring membership stale during shutdown", exc_info=True)

        if metrics_server is not None:
            metrics_server.should_exit = True

        await cache_poller.stop()
        await accounts_cache_warmer.stop()
        await event_loop_lag_monitor.stop()
        await federation_push_scheduler.stop()
        await federation_mirror_scheduler.stop()
        await account_pulse_scheduler.stop()
        await account_resume_scheduler.stop()
        await reset_credit_auto_redeem_scheduler.stop()
        await quota_planner_scheduler.stop()
        await auth_guardian_scheduler.stop()
        await sticky_session_cleanup_scheduler.stop()
        await model_scheduler.stop()
        await api_key_limit_reset_scheduler.stop()
        await seat_quota_scheduler.stop()
        await usage_scheduler.stop()
        if not await shutdown_state.settle_exchanges(shutdown_state.EXCHANGE_SETTLE_S):
            logger.warning("Shutdown timed out with %d token exchanges pending", shutdown_state.get_exchanges())
        try:
            await close_codex_pool()
            await close_http_client()
        finally:
            try:
                if metrics_server_task is not None:
                    await asyncio.wait_for(metrics_server_task, timeout=5)
            except TimeoutError:
                logger.warning("Metrics shutdown timed out")
            except Exception:
                logger.exception("Metrics server failed")
            finally:
                await node_cache.stop()
                shutdown_state.reset()
                mark_process_dead()
                await close_db()


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

    configure_app(app, settings)
    return app


def configure_app(app: FastAPI, settings) -> None:
    app.add_middleware(cast(Any, InFlightMiddleware))
    app.add_middleware(cast(Any, ClientSessionMiddleware))
    app.add_middleware(cast(Any, IdentityMiddleware))
    add_dashboard_auth_proxy_middleware(app)
    app.add_middleware(Aliases)
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
    app.include_router(models)
    register_muse_routes(app)
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

    configure_static_routes(app)


app = create_app()
