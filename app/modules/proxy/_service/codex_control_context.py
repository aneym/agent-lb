from __future__ import annotations

import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, Protocol, TypeVar, cast

from app.core.auth.refresh import RefreshError
from app.core.balancer import (
    TRAFFIC_CLASS_FOREGROUND,
    ResetPreferenceWindow,
    RoutingStrategy,
    TrafficClass,
)
from app.core.clients.proxy import (
    CodexControlResponse,
    ProxyResponseError,
)
from app.core.clients.proxy import codex_control_request as core_codex_control_request
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.db.models import Account
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy._service.support import _RequestLogFailureMetadata
from app.modules.proxy.affinity import _AffinityPolicy
from app.modules.proxy.load_balancer import AccountSelection

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")


class _CodexControlServiceProtocol(Protocol):
    _encryptor: Any
    _load_balancer: Any

    async def _select_account_with_budget_compatible(self, deadline: float, **kwargs: object) -> AccountSelection: ...
    async def _select_account_with_budget(self, deadline: float, **kwargs: Any) -> AccountSelection: ...
    async def _select_codex_control_account_without_budget(
        self,
        *,
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
        prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    ) -> Account | None: ...
    async def _ensure_previsible_unary_fresh_with_failover(self, account: Account, **kwargs: Any) -> Account: ...
    async def _retry_previsible_unary_call_failover(
        self, exc: ProxyResponseError, account: Account, **kwargs: Any
    ) -> tuple[Account, CodexControlResponse] | None: ...
    async def _ensure_fresh_with_budget_or_auth_error(self, account: Account, *, timeout_seconds: float) -> Account: ...
    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None: ...
    async def _write_request_log(self, **kwargs: Any) -> None: ...
    async def _resolve_upstream_route_for_account(
        self, account: Account, *, operation: str
    ) -> ResolvedUpstreamRoute | None: ...


def _service_core_codex_control_request() -> Callable[..., Awaitable[CodexControlResponse]]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(
            Callable[..., Awaitable[CodexControlResponse]],
            getattr(service_module, "core_codex_control_request", core_codex_control_request),
        )
    return core_codex_control_request


def _service_global_or(name: str, fallback: T) -> T:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        return fallback
    return cast(T, getattr(service_module, name, fallback))


def _service_get_settings() -> Any:
    return _service_global_or("get_settings", get_settings)()


def _service_get_settings_cache() -> Any:
    return _service_global_or("get_settings_cache", get_settings_cache)()


def _service_time() -> Any:
    return _service_global_or("time", time)


def _service_global(name: str) -> Any:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is None:
        raise RuntimeError("app.modules.proxy.service is not loaded")
    return getattr(service_module, name)


def _remaining_budget_seconds(deadline: float) -> float:
    return cast(Callable[[float], float], _service_global("_remaining_budget_seconds"))(deadline)


def _raise_proxy_budget_exhausted() -> NoReturn:
    cast(Callable[[], NoReturn], _service_global("_raise_proxy_budget_exhausted"))()


def _raise_proxy_unavailable(message: str) -> NoReturn:
    cast(Callable[[str], NoReturn], _service_global("_raise_proxy_unavailable"))(message)


def _request_log_failure_metadata(exc: ProxyResponseError) -> _RequestLogFailureMetadata:
    return cast(
        Callable[[ProxyResponseError], _RequestLogFailureMetadata], _service_global("_request_log_failure_metadata")
    )(exc)


def _proxy_response_failed_account(exc: ProxyResponseError, fallback: Account) -> Account:
    return cast(Callable[[ProxyResponseError, Account], Account], _service_global("_proxy_response_failed_account"))(
        exc, fallback
    )


def _refresh_error_failed_account(exc: RefreshError, fallback: Account) -> Account:
    return cast(Callable[[RefreshError, Account], Account], _service_global("_refresh_error_failed_account"))(
        exc, fallback
    )


def _prefer_earlier_reset_window(settings: Any) -> ResetPreferenceWindow:
    return cast(Callable[[Any], ResetPreferenceWindow], _service_global("_prefer_earlier_reset_window"))(settings)


def _routing_strategy(settings: Any) -> RoutingStrategy:
    return cast(Callable[[Any], RoutingStrategy], _service_global("_routing_strategy"))(settings)
