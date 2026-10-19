from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NoReturn, Protocol, TypeVar, cast

import aiohttp

from app.core.auth.refresh import RefreshError
from app.core.balancer import ResetPreferenceWindow, RoutingStrategy
from app.core.clients.files import FileProxyError, pop_files_timeout_overrides, push_files_timeout_overrides
from app.core.clients.proxy import ProxyResponseError, UpstreamProxyRouteTrace, filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import openai_error
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesRequest,
)
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.db.models import Account
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy._service.support import (
    _FilePinEntry,
    _request_log_useragent_fields,
    _RequestLogFailureMetadata,
)
from app.modules.proxy.helpers import _header_account_id, _normalize_error_code, _parse_openai_error
from app.modules.proxy.load_balancer import AccountSelection

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
_ResponsesPayloadT = ResponsesRequest | ResponsesCompactRequest


class _FileOpsServiceProtocol(Protocol):
    _encryptor: Any
    _file_account_pin_lock: asyncio.Lock
    _file_account_pins: dict[str, _FilePinEntry]
    _load_balancer: Any
    _FILE_ACCOUNT_PIN_TTL_SECONDS: float

    async def _select_account_with_budget_compatible(self, deadline: float, **kwargs: object) -> AccountSelection: ...
    async def _select_account_with_budget(self, deadline: float, **kwargs: Any) -> AccountSelection: ...
    async def _ensure_previsible_unary_fresh_with_failover(self, account: Account, **kwargs: Any) -> Account: ...
    async def _retry_previsible_unary_call_failover(
        self, exc: ProxyResponseError, account: Account, **kwargs: Any
    ) -> tuple[Account, dict[str, JsonValue]] | None: ...
    async def _ensure_fresh_with_budget_or_auth_error(self, account: Account, *, timeout_seconds: float) -> Account: ...
    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None: ...
    async def _write_request_log(self, **kwargs: Any) -> None: ...
    async def _resolve_upstream_route_for_account(
        self, account: Account, *, operation: str
    ) -> ResolvedUpstreamRoute | None: ...
    async def _proxy_files_call(self, **kwargs: Any) -> tuple[dict[str, JsonValue], str | None]: ...
    async def _pin_file_account(self, file_id: str, account_id: str) -> None: ...
    async def _resolve_file_account(self, file_id: str) -> str | None: ...
    async def _lookup_file_pin(self, file_id: str) -> _FilePinEntry | None: ...
    def _evict_expired_file_pins_locked(self) -> None: ...


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


_FAILED_ACCOUNT_ATTR = "_agent_lb_failed_account"
_REQUEST_TRANSPORT_HTTP = "http"


class _FileTransferMixin:
    async def _proxy_files_call(
        self,
        *,
        log_model: str,
        kind: str,
        api_key: ApiKeyData | None,
        headers: Mapping[str, str],
        invoke: Callable[
            [str, str | None, Mapping[str, str], ResolvedUpstreamRoute | None, UpstreamProxyRouteTrace],
            Awaitable[dict[str, JsonValue]],
        ],
        preferred_account_id: str | None = None,
    ) -> tuple[dict[str, JsonValue], str | None]:

        proxy = cast(_FileOpsServiceProtocol, self)
        filtered = filter_inbound_headers(headers)
        useragent, useragent_group = _request_log_useragent_fields(headers)
        request_id = get_request_id() or ensure_request_id(None)
        start = _service_time().monotonic()
        base_settings = _service_get_settings()
        deadline = start + base_settings.transcription_request_budget_seconds
        account_id_value: str | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        failure_metadata = _RequestLogFailureMetadata()
        route_mode: str | None = None
        route_pool_id: str | None = None
        route_endpoint_id: str | None = None
        route_fallback_used: bool | None = None
        route_fail_closed_reason: str | None = None

        settings = await _service_get_settings_cache().get()
        prefer_earlier_reset = settings.prefer_earlier_reset_accounts
        routing_strategy = _routing_strategy(settings)
        try:
            selection = await proxy._select_account_with_budget_compatible(
                deadline,
                request_id=request_id,
                kind=kind,
                api_key=api_key,
                prefer_earlier_reset_accounts=prefer_earlier_reset,
                routing_strategy=routing_strategy,
                model=None,
                preferred_account_id=preferred_account_id,
                fallback_on_preferred_account_unavailable=preferred_account_id is None,
            )
            account = selection.account
            if not account:
                log_error_code = selection.error_code or "no_accounts"
                log_error_message = selection.error_message or "No active accounts available"
                raise ProxyResponseError(
                    503,
                    openai_error(log_error_code, log_error_message),
                )
            account_id_value = account.id

            async def _call(target: Account) -> dict[str, JsonValue]:
                nonlocal route_mode, route_pool_id, route_endpoint_id, route_fallback_used
                access_token = proxy._encryptor.decrypt(target.access_token_encrypted)
                account_id = _header_account_id(target.chatgpt_account_id)
                route = await proxy._resolve_upstream_route_for_account(target, operation=kind)
                route_trace = UpstreamProxyRouteTrace()
                if route is not None:
                    route_mode = route.mode
                    route_pool_id = route.pool_id
                    route_endpoint_id = route.endpoint_id
                    route_fallback_used = False
                remaining_budget = _remaining_budget_seconds(deadline)
                if remaining_budget <= 0:
                    logger.warning(
                        "%s request budget exhausted before upstream call request_id=%s account_id=%s",
                        kind,
                        request_id,
                        target.id,
                    )
                    _raise_proxy_budget_exhausted()

                timeout_tokens = push_files_timeout_overrides(
                    connect_timeout_seconds=remaining_budget,
                    total_timeout_seconds=remaining_budget,
                )
                try:
                    return await invoke(access_token, account_id, filtered, route, route_trace)
                except FileProxyError as files_exc:
                    raise ProxyResponseError(
                        files_exc.status_code,
                        files_exc.payload,
                        failure_phase=files_exc.failure_phase,
                    ) from files_exc
                finally:
                    if route_trace.mode is not None:
                        route_mode = route_trace.mode
                        route_pool_id = route_trace.pool_id
                        route_endpoint_id = route_trace.endpoint_id
                        route_fallback_used = route_trace.fallback_used
                    pop_files_timeout_overrides(timeout_tokens)

            async def _select_files_failover(excluded_account_ids: set[str]) -> AccountSelection:
                return await proxy._select_account_with_budget(
                    deadline,
                    request_id=request_id,
                    kind=kind,
                    api_key=api_key,
                    prefer_earlier_reset_accounts=prefer_earlier_reset,
                    routing_strategy=routing_strategy,
                    model=None,
                    preferred_account_id=preferred_account_id,
                    exclude_account_ids=excluded_account_ids,
                )

            try:
                account = await proxy._ensure_previsible_unary_fresh_with_failover(
                    account,
                    deadline=deadline,
                    request_id=request_id,
                    kind=kind,
                    select_next_account=_select_files_failover,
                    strict_account_id=preferred_account_id,
                )
                account_id_value = account.id
                result = await _call(account)
                await proxy._load_balancer.record_success(account)
                log_status = "success"
                return result, account_id_value
            except RefreshError as refresh_exc:
                if refresh_exc.is_permanent:
                    failed_account = _refresh_error_failed_account(refresh_exc, account)
                    account_id_value = failed_account.id
                    await proxy._load_balancer.mark_permanent_failure(failed_account, refresh_exc.code)
                raise ProxyResponseError(
                    401,
                    openai_error(
                        "invalid_api_key",
                        refresh_exc.message,
                        error_type="invalid_request_error",
                    ),
                ) from refresh_exc
            except ProxyResponseError as exc:
                if exc.status_code != 401:
                    failover = await proxy._retry_previsible_unary_call_failover(
                        exc,
                        account,
                        deadline=deadline,
                        select_next_account=_select_files_failover,
                        call_next=_call,
                        strict_account_id=preferred_account_id,
                    )
                    if failover is not None:
                        account, result = failover
                        account_id_value = account.id
                        log_status = "success"
                        return result, account_id_value
                    failed_account = _proxy_response_failed_account(exc, account)
                    account_id_value = failed_account.id
                    await proxy._handle_proxy_error(failed_account, exc)
                    raise
                try:
                    remaining_budget = _remaining_budget_seconds(deadline)
                    if remaining_budget <= 0:
                        logger.warning(
                            "%s request budget exhausted before forced refresh retry request_id=%s account_id=%s",
                            kind,
                            request_id,
                            account.id,
                        )
                        _raise_proxy_budget_exhausted()
                    try:
                        account = await proxy._ensure_previsible_unary_fresh_with_failover(
                            account,
                            deadline=deadline,
                            request_id=request_id,
                            kind=kind,
                            select_next_account=_select_files_failover,
                            strict_account_id=preferred_account_id,
                            force=True,
                        )
                    except ProxyResponseError as refresh_failover_exc:
                        failed_account = _proxy_response_failed_account(refresh_failover_exc, account)
                        account_id_value = failed_account.id
                        await proxy._handle_proxy_error(failed_account, refresh_failover_exc)
                        raise
                    account_id_value = account.id
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        failed_account = _refresh_error_failed_account(refresh_exc, account)
                        account_id_value = failed_account.id
                        await proxy._load_balancer.mark_permanent_failure(failed_account, refresh_exc.code)
                    raise exc
                except (aiohttp.ClientError, asyncio.TimeoutError) as timeout_exc:
                    logger.warning(
                        "%s forced refresh/connect failed request_id=%s account_id=%s",
                        kind,
                        request_id,
                        account.id,
                        exc_info=True,
                    )
                    _raise_proxy_unavailable(str(timeout_exc) or "Request to upstream timed out")
                try:
                    result = await _call(account)

                    account_id_value = account.id
                    await proxy._load_balancer.record_success(account)
                    log_status = "success"
                    return result, account_id_value
                except ProxyResponseError as retry_exc:
                    await proxy._handle_proxy_error(account, retry_exc)
                    if retry_exc.status_code == 401:
                        selection = await proxy._select_account_with_budget(
                            deadline,
                            request_id=request_id,
                            kind=kind,
                            api_key=api_key,
                            prefer_earlier_reset_accounts=prefer_earlier_reset,
                            routing_strategy=routing_strategy,
                            model=None,
                            preferred_account_id=preferred_account_id,
                            fallback_on_preferred_account_unavailable=preferred_account_id is None,
                            exclude_account_ids={account.id},
                        )
                        if selection.account is not None:
                            account = selection.account
                            account_id_value = account.id
                            account = await proxy._ensure_fresh_with_budget_or_auth_error(
                                account,
                                timeout_seconds=_remaining_budget_seconds(deadline),
                            )
                            try:
                                result = await _call(account)
                                await proxy._load_balancer.record_success(account)
                                log_status = "success"
                                return result, account_id_value
                            except ProxyResponseError as failover_exc:
                                await proxy._handle_proxy_error(account, failover_exc)
                                raise
                    raise
        except ProxyResponseError as exc:
            failed_account = getattr(exc, _FAILED_ACCOUNT_ATTR, None)
            if isinstance(failed_account, Account):
                account_id_value = failed_account.id
            failure_metadata = _request_log_failure_metadata(exc)
            error = _parse_openai_error(exc.payload)
            log_error_code = log_error_code or _normalize_error_code(
                error.code if error else None,
                error.type if error else None,
            )
            log_error_message = log_error_message or (error.message if error else None)
            raise
        except UpstreamProxyRouteError as exc:
            route_fail_closed_reason = exc.reason
            log_error_code = "upstream_proxy_unavailable"
            log_error_message = exc.reason
            raise ProxyResponseError(
                502,
                openai_error("upstream_proxy_unavailable", f"Upstream proxy route unavailable: {exc.reason}"),
            ) from exc
        finally:
            await proxy._write_request_log(
                account_id=account_id_value,
                api_key=api_key,
                request_id=request_id,
                model=log_model,
                latency_ms=int((_service_time().monotonic() - start) * 1000),
                status=log_status,
                error_code=log_error_code,
                error_message=log_error_message,
                transport=_REQUEST_TRANSPORT_HTTP,
                failure_phase=failure_metadata.failure_phase,
                failure_detail=failure_metadata.failure_detail,
                failure_exception_type=failure_metadata.failure_exception_type,
                upstream_status_code=failure_metadata.upstream_status_code,
                upstream_error_code=failure_metadata.upstream_error_code,
                bridge_stage=failure_metadata.bridge_stage,
                upstream_proxy_route_mode=route_mode,
                upstream_proxy_pool_id=route_pool_id,
                upstream_proxy_endpoint_id=route_endpoint_id,
                upstream_proxy_fallback_used=route_fallback_used if route_endpoint_id else None,
                upstream_proxy_fail_closed_reason=route_fail_closed_reason,
                useragent=useragent,
                useragent_group=useragent_group,
            )
