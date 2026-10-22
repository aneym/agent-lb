from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Awaitable
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlparse

import aiohttp

from app.core.config.settings import bridge_advertise_hostname_is_replica_specific
from app.db.session import SessionLocal
from app.modules.proxy.durable_bridge_repository import missing_durable_bridge_tables
from app.modules.proxy.ring_membership import RING_HEARTBEAT_INTERVAL_SECONDS, RING_STALE_THRESHOLD_SECONDS

bridge_logger = logging.getLogger("app.main")


class _RingMembershipReader(Protocol):
    def list_active(
        self,
        stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS,
        *,
        require_endpoint: bool = False,
    ) -> Awaitable[list[str]]: ...


async def ensure_bridge_durable_schema_ready(settings) -> bool:
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
    bridge_logger.warning(
        "HTTP bridge durable schema is missing required tables but startup fail-fast is disabled",
        extra={"missing_tables": missing_tables},
    )
    return False


async def wait_for_bridge_advertise_endpoint(
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
            bridge_logger.debug(
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


async def validate_bridge_advertise_endpoint_for_multi_replica(
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
