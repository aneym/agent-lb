from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from app.core.balancer.state import AccountState
from app.core.config.settings import get_settings
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    account_cap_rejections_total,
    account_lease_acquired_total,
    account_lease_active,
    account_lease_released_total,
    account_lease_stale_reclaimed_total,
)
from app.modules.proxy.runtime_state import AccountLeaseKind


def stale_ttl_seconds(kind: Literal["response_create", "stream"], settings: object) -> float:
    ttl_seconds = float(getattr(settings, "proxy_account_lease_ttl_seconds", 900.0))
    if kind != "stream":
        return ttl_seconds
    valid_stream_budget_seconds = max(
        ttl_seconds,
        float(getattr(settings, "proxy_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_stream_request_budget_seconds", ttl_seconds)),
        float(getattr(settings, "http_responses_session_bridge_request_budget_seconds", ttl_seconds)),
    )
    return max(ttl_seconds, valid_stream_budget_seconds + 60.0)


def filter_states_for_account_caps(
    states: Iterable[AccountState],
    *,
    lease_kind: AccountLeaseKind | None,
) -> list[AccountState]:
    if lease_kind is None:
        return list(states)
    settings = get_settings()
    filtered: list[AccountState] = []
    for state in states:
        if lease_kind == "response_create":
            cap = settings.proxy_account_response_create_limit
            if cap > 0 and state.inflight_response_creates >= cap:
                continue
        else:
            cap = settings.proxy_account_stream_limit
            if cap > 0 and state.inflight_streams >= cap:
                continue
        filtered.append(state)
    return filtered


def account_cap_error_code(lease_kind: AccountLeaseKind | None) -> str | None:
    if lease_kind == "response_create":
        return "account_response_create_cap"
    if lease_kind == "stream":
        return "account_stream_cap"
    return None


def record_account_lease_acquired(kind: AccountLeaseKind) -> None:
    if not PROMETHEUS_AVAILABLE:
        return
    if account_lease_acquired_total is not None:
        account_lease_acquired_total.labels(kind=kind).inc()
    if account_lease_active is not None:
        account_lease_active.labels(kind=kind).inc()


def record_account_lease_released(kind: AccountLeaseKind, reason: str) -> None:
    if not PROMETHEUS_AVAILABLE:
        return
    if account_lease_released_total is not None:
        account_lease_released_total.labels(kind=kind, reason=reason).inc()
    if account_lease_active is not None:
        account_lease_active.labels(kind=kind).dec()


def record_account_lease_stale_reclaimed(kind: AccountLeaseKind) -> None:
    if PROMETHEUS_AVAILABLE and account_lease_stale_reclaimed_total is not None:
        account_lease_stale_reclaimed_total.labels(kind=kind).inc()


def record_account_cap_rejection(kind: AccountLeaseKind | None) -> None:
    if kind is None:
        return
    if PROMETHEUS_AVAILABLE and account_cap_rejections_total is not None:
        account_cap_rejections_total.labels(kind=kind).inc()
