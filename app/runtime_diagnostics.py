from __future__ import annotations

import logging
from ipaddress import ip_network
from typing import Any
from urllib.parse import urlparse

from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE

diagnostics_logger = logging.getLogger("app.main")


def log_runtime_diagnostic_fingerprint(settings, *, bridge_durable_schema_ready: bool | None) -> None:
    fingerprint = _runtime_diagnostic_fingerprint(settings, bridge_durable_schema_ready=bridge_durable_schema_ready)
    diagnostics_logger.info(
        (
            "agent_lb_runtime_fingerprint database_backend=%s data_dir=%s dashboard_auth_mode=%s "
            "trusted_proxy_cidrs=%s proxy_unauthenticated_cidrs=%s proxy_tailnet_allowed=%s "
            "metrics_enabled=%s metrics_available=%s metrics_bind=%s:%s "
            "response_create_cap=%s stream_cap=%s bridge_enabled=%s bridge_schema_status=%s"
        ),
        fingerprint["database_backend"],
        fingerprint["data_dir"],
        fingerprint["dashboard_auth_mode"],
        fingerprint["firewall_trusted_proxy_cidrs_count"],
        fingerprint["proxy_unauthenticated_client_cidrs_count"],
        fingerprint["proxy_unauthenticated_tailnet_allowed"],
        fingerprint["metrics_enabled"],
        fingerprint["metrics_available"],
        fingerprint["metrics_host"],
        fingerprint["metrics_port"],
        fingerprint["proxy_account_response_create_limit"],
        fingerprint["proxy_account_stream_limit"],
        fingerprint["http_responses_session_bridge_enabled"],
        fingerprint["bridge_schema_status"],
        extra=fingerprint,
    )


def _runtime_diagnostic_fingerprint(settings, *, bridge_durable_schema_ready: bool | None) -> dict[str, Any]:
    return {
        "event": "agent_lb_runtime_fingerprint",
        "database_backend": _database_backend(settings.database_url),
        "data_dir": str(settings.data_dir),
        "dashboard_auth_mode": _enum_value(settings.dashboard_auth_mode),
        "firewall_trust_proxy_headers": settings.firewall_trust_proxy_headers,
        "firewall_trusted_proxy_cidrs_count": len(settings.firewall_trusted_proxy_cidrs),
        "proxy_unauthenticated_client_cidrs_count": len(settings.proxy_unauthenticated_client_cidrs),
        "proxy_unauthenticated_tailnet_allowed": _cidrs_include_tailnet(settings.proxy_unauthenticated_client_cidrs),
        "metrics_enabled": settings.metrics_enabled,
        "metrics_available": PROMETHEUS_AVAILABLE,
        "metrics_host": settings.metrics_host,
        "metrics_port": settings.metrics_port,
        "log_format": settings.log_format,
        "proxy_account_response_create_limit": settings.proxy_account_response_create_limit,
        "proxy_account_stream_limit": settings.proxy_account_stream_limit,
        "http_responses_session_bridge_enabled": settings.http_responses_session_bridge_enabled,
        "http_responses_session_bridge_instance_ring_size": len(settings.http_responses_session_bridge_instance_ring),
        "http_responses_session_bridge_max_sessions": settings.http_responses_session_bridge_max_sessions,
        "http_responses_session_bridge_queue_limit": settings.http_responses_session_bridge_queue_limit,
        "http_responses_session_bridge_response_create_concurrency": (
            settings.http_responses_session_bridge_response_create_concurrency
        ),
        "bridge_advertise_base_url_configured": settings.http_responses_session_bridge_advertise_base_url is not None,
        "bridge_schema_status": _bridge_schema_status(
            settings.http_responses_session_bridge_enabled,
            bridge_durable_schema_ready,
        ),
    }


def _database_backend(database_url: str) -> str:
    scheme = urlparse(database_url).scheme
    if not scheme:
        return "unknown"
    return scheme.split("+", maxsplit=1)[0]


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _bridge_schema_status(bridge_enabled: bool, bridge_durable_schema_ready: bool | None) -> str:
    if not bridge_enabled:
        return "disabled"
    if bridge_durable_schema_ready is None:
        return "not_checked"
    return "ready" if bridge_durable_schema_ready else "missing"


def _cidrs_include_tailnet(cidrs: list[str]) -> bool:
    tailnet_v4 = ip_network("100.64.0.0/10")
    tailnet_v6 = ip_network("fd7a:115c:a1e0::/48")
    for cidr in cidrs:
        try:
            network = ip_network(cidr, strict=False)
        except ValueError:
            continue
        if network.version == 4 and network.overlaps(tailnet_v4):
            return True
        if network.version == 6 and network.overlaps(tailnet_v6):
            return True
    return False
