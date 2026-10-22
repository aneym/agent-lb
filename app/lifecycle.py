from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from importlib import import_module
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlparse

import aiohttp
from fastapi import FastAPI

from app.core.auth.guardian import build_auth_guardian_scheduler
from app.core.bootstrap import ensure_auto_bootstrap_token, log_bootstrap_token
from app.core.clients.codex_pool import close_codex_pool
from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import bridge_advertise_hostname_is_replica_specific, get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.identity_nodes import IdentityNodeCache
from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, make_scrape_registry, mark_process_dead
from app.core.metrics.serve import serve_metrics
from app.core.openai.model_refresh_scheduler import build_model_refresh_scheduler
from app.core.resilience.event_loop_lag_monitor import build_event_loop_lag_monitor
from app.core.usage.refresh_scheduler import build_usage_refresh_scheduler
from app.db.session import SessionLocal, close_db, init_background_db, init_db
from app.modules.account_schedule.scheduler import build_account_resume_scheduler
from app.modules.accounts.cache_warmer import build_accounts_cache_warmer
from app.modules.accounts.pulse import build_account_pulse_scheduler
from app.modules.accounts.reset_credit_scheduler import build_reset_credit_auto_redeem_scheduler
from app.modules.api_keys.reset_scheduler import build_api_key_limit_reset_scheduler
from app.modules.federation.push import build_federation_push_scheduler
from app.modules.federation.scheduler import build_federation_mirror_scheduler
from app.modules.pools.seat_quota_scheduler import build_seat_quota_scheduler
from app.modules.proxy.durable_bridge_repository import missing_durable_bridge_tables
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.ring_membership import (
    RING_HEARTBEAT_INTERVAL_SECONDS,
    RING_STALE_GRACE_SECONDS,
    RING_STALE_THRESHOLD_SECONDS,
    RingMembershipService,
)
from app.modules.quota_planner.scheduler import build_quota_planner_scheduler
from app.modules.sticky_sessions.cleanup_scheduler import build_sticky_session_cleanup_scheduler
from app.modules.usage.additional_quota_keys import reload_additional_quota_registry
from app.runtime_diagnostics import log_runtime_diagnostic_fingerprint as _log_runtime_diagnostic_fingerprint

logger = logging.getLogger(__name__)


class _RingMembershipReader(Protocol):
    def list_active(
        self,
        stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS,
        *,
        require_endpoint: bool = False,
    ) -> Awaitable[list[str]]: ...


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.core.startup as startup_module

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
        bridge_durable_schema_ready = await _ensure_bridge_durable_schema_ready(settings)
    _log_runtime_diagnostic_fingerprint(settings, bridge_durable_schema_ready=bridge_durable_schema_ready)
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
    if settings.metrics_enabled and PROMETHEUS_AVAILABLE:
        import uvicorn

        scrape_registry = make_scrape_registry()
        prometheus_module = import_module("prometheus_client")
        make_asgi_app = getattr(prometheus_module, "make_asgi_app")
        metrics_app = make_asgi_app(registry=scrape_registry)
        config = uvicorn.Config(
            metrics_app,
            host=settings.metrics_host,
            port=settings.metrics_port,
            log_level="warning",
        )
        metrics_server = uvicorn.Server(config)

        metrics_server_task = asyncio.create_task(serve_metrics(metrics_server, settings.metrics_port))
    elif settings.metrics_enabled:
        logger.warning("Metrics endpoint enabled but prometheus-client is not installed")

    async def _complete_bridge_registration(svc: RingMembershipService, iid: str) -> None:
        if bridge_endpoint_base_url is None:
            await _activate_bridge_membership(svc, iid)
            startup_module.mark_bridge_registration_complete()
            return
        await _validate_bridge_advertise_endpoint_for_multi_replica(
            svc=svc,
            settings=settings,
            instance_id=iid,
            endpoint_base_url=bridge_endpoint_base_url,
        )
        await svc.register(iid, endpoint_base_url=None)
        await _wait_for_bridge_advertise_endpoint(
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
                logger.warning("Ring registration attempt %d failed, retrying in %.0fs", attempt, delay, exc_info=True)
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


async def _ensure_bridge_durable_schema_ready(settings) -> bool:
    if not settings.http_responses_session_bridge_enabled:
        return False
    session = SessionLocal()
    try:
        missing_tables = await missing_durable_bridge_tables(session)
    finally:
        await session.close()
    if not missing_tables:
        return True
    missing = ", ".join(missing_tables)
    if settings.database_migrations_fail_fast:
        raise RuntimeError(f"HTTP bridge durable schema is missing required tables: {missing}")
    logger.warning(
        "HTTP bridge durable schema is missing required tables but startup fail-fast is disabled",
        extra={"missing_tables": missing_tables},
    )
    return False


async def _wait_for_bridge_advertise_endpoint(
    bridge_endpoint_base_url: str | None,
    *,
    connect_timeout_seconds: float,
) -> None:
    local_port = _local_api_port()
    if bridge_endpoint_base_url is None and local_port is None:
        raise RuntimeError(
            "Cannot determine local bridge listener port for registration probe; "
            "set PORT or configure http_responses_session_bridge_advertise_base_url"
        )
    probe_base_url = bridge_endpoint_base_url or f"http://127.0.0.1:{local_port}"
    probe_base_url = probe_base_url.rstrip("/")
    probe_url = f"{probe_base_url}/health/live"
    timeout = aiohttp.ClientTimeout(
        total=connect_timeout_seconds,
        sock_connect=connect_timeout_seconds,
        sock_read=connect_timeout_seconds,
    )
    max_probe_wait_seconds = max(connect_timeout_seconds, 5.0)
    deadline = time.monotonic() + max_probe_wait_seconds
    await asyncio.sleep(0)
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.get(probe_url) as response:
                    if response.status == 200:
                        return
        except Exception:
            logger.debug(
                "Bridge advertise endpoint not yet reachable",
                extra={"probe_url": probe_url, "attempt": attempt},
                exc_info=True,
            )
        delay = min(0.5 * (2 ** min(attempt - 1, 4)), 5.0)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(delay, remaining))
    raise RuntimeError(
        f"Bridge advertise endpoint did not become reachable before registration probe deadline: {probe_url}"
    )


def _local_api_port() -> int | None:
    raw = os.getenv("PORT")
    port = _parse_port_value(raw.strip()) if raw is not None else None
    if port is None:
        port = _port_from_argv()
    return port


def _parse_port_value(raw: str) -> int | None:
    try:
        port = int(raw)
    except ValueError:
        return None
    if port <= 0:
        return None
    return port


def _port_from_argv() -> int | None:
    args = tuple(sys.argv[1:])
    for index, value in enumerate(args):
        if value == "--port" and index + 1 < len(args):
            return _parse_port_value(args[index + 1])
        if value.startswith("--port="):
            return _parse_port_value(value.split("=", 1)[1])
    return None


async def _validate_bridge_advertise_endpoint_for_multi_replica(
    svc: _RingMembershipReader,
    *,
    settings,
    instance_id: str,
    endpoint_base_url: str | None,
) -> None:
    if endpoint_base_url is None:
        return
    hostname = urlparse(endpoint_base_url).hostname
    if hostname is None:
        raise RuntimeError("http_responses_session_bridge_advertise_base_url must include a valid hostname")
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        parsed_ip = None
    if (parsed_ip is not None and parsed_ip.is_loopback) or hostname == "localhost":
        configured_multi_replica = len(settings.http_responses_session_bridge_instance_ring) > 1
        if configured_multi_replica:
            raise RuntimeError(
                "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
            )
        try:
            active_instances = await svc.list_active(stale_threshold_seconds=RING_HEARTBEAT_INTERVAL_SECONDS)
        except Exception:
            active_instances = []
        if any(active_instance != instance_id for active_instance in active_instances):
            raise RuntimeError(
                "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
            )
        return
    if not bridge_advertise_hostname_is_replica_specific(hostname, instance_id=instance_id):
        raise RuntimeError(
            "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
        )
