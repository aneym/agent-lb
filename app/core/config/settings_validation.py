from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections.abc import Mapping
from ipaddress import ip_network

from app.core.config.settings_defaults import _effective_environ

logger = logging.getLogger(__name__)
AUTH_BASE_URL = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_ORIGINATOR = "codex_chatgpt_desktop"
OAUTH_SCOPE = "openid profile email"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_CALLBACK_PORT = 1455
_REMOVED_SETTINGS: tuple[str, ...] = (
    "CODEX_LB_AUTH_BASE_URL",
    "CODEX_LB_OAUTH_CLIENT_ID",
    "CODEX_LB_OAUTH_ORIGINATOR",
    "CODEX_LB_OAUTH_SCOPE",
    "CODEX_LB_OAUTH_REDIRECT_URI",
    "CODEX_LB_OAUTH_CALLBACK_PORT",
    "CODEX_LB_AUTH_GUARDIAN_INTERVAL_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_MAX_REFRESH_AGE_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_BATCH_SIZE",
    "CODEX_LB_AUTH_GUARDIAN_CONCURRENCY",
    "CODEX_LB_AUTH_GUARDIAN_JITTER_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_FAILURE_BACKOFF_BASE_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_FAILURE_BACKOFF_MAX_SECONDS",
    "CODEX_LB_LOG_PROXY_REQUEST_SHAPE",
    "CODEX_LB_LOG_PROXY_REQUEST_SHAPE_RAW_CACHE_KEY",
    "CODEX_LB_LOG_PROXY_REQUEST_PAYLOAD",
    "CODEX_LB_LOG_PROXY_SERVICE_TIER_TRACE",
    "CODEX_LB_LOG_UPSTREAM_REQUEST_SUMMARY",
    "CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD",
    "CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT",
    "CODEX_LB_BULKHEAD_PROXY_WEBSOCKET_LIMIT",
    "CODEX_LB_BULKHEAD_PROXY_COMPACT_LIMIT",
    "CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS",
    "CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS",
    "CODEX_LB_QUOTA_PLANNER_TICK_SECONDS",
    "CODEX_LB_AUTOMATIONS_SCHEDULER_INTERVAL_SECONDS",
    "CODEX_LB_MODEL_REGISTRY_REFRESH_INTERVAL_SECONDS",
    "CODEX_LB_STICKY_SESSION_CLEANUP_INTERVAL_SECONDS",
    "CODEX_LB_CODEX_FINGERPRINT_OS",
    "CODEX_LB_CODEX_FINGERPRINT_ARCH",
    "CODEX_LB_CODEX_FINGERPRINT_TERMINAL",
    "CODEX_LB_LIVE_USAGE_WRITE_MIN_INTERVAL_SECONDS",
    "CODEX_LB_LIVE_USAGE_QUEUE_SIZE",
    "CODEX_LB_REQUEST_LOG_COUNT_CACHE_TTL_SECONDS",
    "CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    "CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS",
    "CODEX_LB_MEMORY_WARNING_THRESHOLD_MB",
    "CODEX_LB_IMAGES_HOST_MODEL",
    "CODEX_LB_IMAGES_MAX_PARTIAL_IMAGES",
    "CODEX_LB_DATABASE_BACKGROUND_POOL_SIZE",
    "CODEX_LB_DATABASE_BACKGROUND_MAX_OVERFLOW",
    "CODEX_LB_DATABASE_POOL_TIMEOUT_SECONDS",
    "CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS",
    "CODEX_LB_DRAIN_PRIMARY_THRESHOLD_PCT",
    "CODEX_LB_DRAIN_SECONDARY_THRESHOLD_PCT",
    "CODEX_LB_DRAIN_ERROR_WINDOW_SECONDS",
    "CODEX_LB_DRAIN_ERROR_COUNT_THRESHOLD",
    "CODEX_LB_PROBE_QUIET_SECONDS",
    "CODEX_LB_PROBE_SUCCESS_STREAK_REQUIRED",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_CANARY_PERCENT",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ALLOW_API_KEY_IDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_DENY_API_KEY_IDS",
)


def warn_removed_settings(environ: Mapping[str, str] | None = None) -> list[str]:
    if environ is None:
        source: Mapping[str, str | None] = _effective_environ()
    else:
        source = environ
    found = [name for name in _REMOVED_SETTINGS if name in source]
    if found:
        logger.warning(
            "removed setting(s) ignored: %s — values are now fixed; see PRINCIPLES.md P2 / issue #1340",
            ", ".join(found),
        )
    return found


type StringListInput = str | list[str] | None
type OptionalStringInput = str | None
type ModelContextWindowOverridesInput = str | dict[str, int] | None


def identity_handle(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub("[^a-z0-9]+", "-", ascii_value).strip("-")[:limit].rstrip("-")
    return slug or None


def _validate_context_window_entries(data: Mapping[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for k, v in data.items():
        if isinstance(v, bool):
            raise TypeError(f"model_context_window_overrides value for '{k}' must be a positive integer, got bool")
        if not isinstance(v, int):
            raise TypeError(
                f"model_context_window_overrides value for '{k}' must be a positive integer, got {type(v).__name__}"
            )
        if v <= 0:
            raise ValueError(f"model_context_window_overrides value for '{k}' must be a positive integer, got {v}")
        result[str(k)] = v
    return result


def _parse_port_value(raw: str) -> int | None:
    try:
        port = int(raw)
    except ValueError:
        return None
    if port <= 0:
        return None
    return port


def _configured_http_port() -> int:
    raw_env_port = os.getenv("PORT")
    if raw_env_port is not None:
        parsed_env_port = _parse_port_value(raw_env_port.strip())
        if parsed_env_port is not None:
            return parsed_env_port
    return 2455


def _normalize_cidr_list(value: StringListInput, *, field_name: str, invalid_label: str) -> list[str]:
    if value is None:
        return []
    cidrs: list[str] = []
    if isinstance(value, str):
        entries = [entry.strip() for entry in value.split(",")]
        cidrs = [entry for entry in entries if entry]
    elif isinstance(value, list):
        for entry in value:
            if isinstance(entry, str):
                cidr = entry.strip()
                if cidr:
                    cidrs.append(cidr)
    else:
        raise TypeError(f"{field_name} must be a list or comma-separated string")
    for cidr in cidrs:
        try:
            ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid {invalid_label}: {cidr}") from exc
    return cidrs
