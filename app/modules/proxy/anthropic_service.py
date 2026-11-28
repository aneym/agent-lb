from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Coroutine, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any

import aiohttp
from yarl import URL

from app.core import upload_admission
from app.core.anthropic.entrypoint_label import relabel_billing_block
from app.core.anthropic.extra_usage import EXTRA_USAGE_QUOTA_KEY as _ANTHROPIC_EXTRA_USAGE_QUOTA_KEY
from app.core.anthropic.extra_usage import (
    extra_usage_blocked,
)
from app.core.anthropic.extra_usage import (
    extra_usage_tripwire_error as _extra_usage_tripwire_error,
)
from app.core.anthropic.extra_usage import (
    extra_usage_tripwire_header as _extra_usage_tripwire_header,
)
from app.core.anthropic.models import (
    AnthropicErrorEvent,
    AnthropicMessageRequest,
    AnthropicUsage,
    cache_creation_1h_tokens,
)
from app.core.anthropic.parsing import (
    AnthropicResponseMetadata,
)
from app.core.anthropic.parsing import (
    collect_response_from_chunk as _collect_response_from_chunk,
)
from app.core.anthropic.parsing import (
    parse_json_response as _parse_json_response,
)
from app.core.anthropic.quota_headers import capture_unified_headers
from app.core.anthropic.quota_headers import parse_unified_utilization as _parse_unified_utilization
from app.core.anthropic.quota_keys import model_quota_key
from app.core.anthropic.response_observability import observe_anthropic_response, upstream_requested_model
from app.core.auth.refresh import classify_refresh_error
from app.core.balancer import TRAFFIC_CLASS_FOREGROUND, TrafficClass
from app.core.balancer.error_rules import match_error_rule
from app.core.balancer.exclusions import active_model_exclusions
from app.core.balancer.types import UpstreamError
from app.core.clients import upstream_body
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.eval_guard import strip_eval_web_tools
from app.core.identity import RequestIdentity, attach_member, get_request_identity
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    get_anthropic_compat_profile,
)
from app.core.providers.request_body import prepare_provider_body
from app.core.utils.client_session import get_dispatch_id
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, StickySessionKind, UsageHistory
from app.modules.accounts.credits import (
    CREDITS_USAGE_WINDOW,
    OPENROUTER_PROVIDER_NAME,
    credits_exhausted,
    window_from_usage,
)
from app.modules.accounts.subscription_status import ANTHROPIC_OAUTH_ORGANIZATION_BLOCK
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import limit_backoff, quota_cooldown_store, selection_refusal
from app.modules.proxy._service.support import _request_log_useragent_fields
from app.modules.proxy.account_warmup import WarmupBusy, admitted_http_session
from app.modules.proxy.anthropic_account_guard import (
    AccountLeaseHolder,
    AnthropicCircuitGuard,
    CircuitBreakerOpenError,
    StallCatcher,
    UpstreamTruncatedEvent,
    anthropic_fast_mode_requested,
    anthropic_reasoning_effort,
    anthropic_request_session_id,
    anthropic_session_header,
    circuit_open_error,
    close_async_iterator,
    count_tokens_quota_key,
    derive_anthropic_session_material,
    frame_end_to_end_headers,
    hash_for_key,
    is_client_stream_error,
    is_fable_model,
    messages_provider_name,
    messages_quota_key,
    messages_sticky_key,
    no_available_accounts_code,
    provider_label,
    provider_name_for_model,
    quota_cooldown_code,
    release_upstream_response,
    request_id_from_headers,
    resolve_planner_model,
    sse_event_boundary,
    stall_bounded_chunks,
    sticky_prefix,
)
from app.modules.proxy.anthropic_account_guard import (
    messages_affinity_quota_key as _messages_affinity_quota_key,
)
from app.modules.proxy.anthropic_cache_policy import apply_cache_tier as _apply_cache_tier
from app.modules.proxy.anthropic_connect_retry import (
    ConnectRetryingResponse,
    UpstreamStallTimeout,
    compression_deadline_error,
)
from app.modules.proxy.anthropic_outbound_headers import build_anthropic_headers
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError, require_claude_code_client
from app.modules.proxy.anthropic_rate_limits import (
    kimi_monthly_rate_limit_error,
    rate_limit_error_from_response,
)
from app.modules.proxy.anthropic_retry_backoff import overloaded_backoff_jitter
from app.modules.proxy.anthropic_selection_details import AnthropicSelectionDetails
from app.modules.proxy.anthropic_thread_anchor import ThreadAnchor
from app.modules.proxy.anthropic_transient_status import TRANSIENT_UPSTREAM_STATUSES, exhausted_error_code
from app.modules.proxy.anthropic_transport import AnthropicTransport
from app.modules.proxy.anthropic_upstream_error_body import (
    error_type_for_status,
    read_error_details,
    read_error_message,
)
from app.modules.proxy.caller_policy import resolve_caller_selection_policy
from app.modules.proxy.claude_codex_bridge import estimate_claude_input_tokens
from app.modules.proxy.claude_model_identity import with_route_model_identity
from app.modules.proxy.compat_errors import STOP_STATUS, pool_exhausted, refresh_due, refuses_plan, rejects_key
from app.modules.proxy.error_rule_actions import (
    apply_error_rule,
    rule_client_message,
    rule_error_code,
    rule_error_message,
)
from app.modules.proxy.load_balancer import LoadBalancer, selectable_accounts
from app.modules.proxy.mirror_tokens import MirrorTokenRefresh
from app.modules.proxy.openrouter_usage import chat_usage, collect_chat_usage
from app.modules.proxy.quota_eligibility_cache import AnthropicQuotaEligibility, get_quota_eligibility_cache
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.request_policy import resolve_model_alias
from app.modules.proxy.runtime_state import AccountLease, AccountLeaseKind
from app.modules.usage.anthropic_live_quota import observe_anthropic_quota

logger = logging.getLogger(__name__)

_STREAM_CHUNK_SIZE = 8192
_MAX_SELECTION_ATTEMPTS = 4


_ANTHROPIC_FABLE_ACCESS_QUOTA_KEY = "anthropic_fable_access"
_ANTHROPIC_FABLE_ACCESS_WINDOW = "primary"


_ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"
_ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW = "primary"


_FABLE_SCOPED_FRESH_SECONDS = 21600
_FRAME_UNAVAILABLE_STATUSES = frozenset(
    {AccountStatus.PAUSED, AccountStatus.REAUTH_REQUIRED, AccountStatus.EXCHANGE_UNCERTAIN, AccountStatus.DEACTIVATED}
)


_OVERLOADED_RETRY_SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep


_CONNECT_RETRY_SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep


_OVERLOADED_RETRY_JITTER: Callable[[], float] = overloaded_backoff_jitter


def _overloaded_backoff_seconds(attempt: int) -> float:
    base = min(0.25 * (2**attempt), 2.0)
    return base + _OVERLOADED_RETRY_JITTER()


_POOL_WAIT_MIN_POLL_SECONDS = 5.0
_POOL_WAIT_MAX_POLL_SECONDS = 300.0
_POOL_WAIT_MAX_JITTER_SECONDS = 5.0


def _default_pool_wait_jitter() -> float:
    return random.uniform(0.0, _POOL_WAIT_MAX_JITTER_SECONDS)


_POOL_WAIT_CLOCK: Callable[[], float] = time.time
_POOL_WAIT_SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep
_POOL_WAIT_JITTER: Callable[[], float] = _default_pool_wait_jitter


@dataclass(frozen=True, slots=True)
class AnthropicProxyStream:
    body: AsyncIterator[bytes]
    media_type: str


@dataclass(frozen=True, slots=True)
class AnthropicResolvedMessageRequest:
    payload: AnthropicMessageRequest
    provider_name: str
    quota_key: str


@dataclass(frozen=True, slots=True)
class AnthropicResolvedCountTokensRequest:
    body: Mapping[str, Any]
    model: str
    provider_name: str


@dataclass(frozen=True, slots=True)
class AnthropicCountTokensResult:
    status_code: int
    body: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class AnthropicFrameResult:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


def _upload_admission_error(exc: upload_admission.UploadAdmissionTimeout) -> tuple[str, str]:
    code = (
        "upload_admission_rejected"
        if isinstance(exc, upload_admission.UploadAdmissionRejected)
        else "upload_admission_timeout"
    )
    return code, upload_admission.failure_message(exc)


class AnthropicProxyService(AnthropicSelectionDetails, MirrorTokenRefresh, AnthropicTransport):
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._load_balancer = LoadBalancer(repo_factory)
        self._encryptor = TokenEncryptor()
        self._pending_request_logs: set[asyncio.Task[None]] = set()
        self._pending_bookkeeping: set[asyncio.Task[None]] = set()

    async def stream_messages(
        self,
        payload: AnthropicMessageRequest,
        inbound_headers: Mapping[str, str],
        *,
        openai_body: dict[str, Any] | None = None,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
    ) -> AnthropicProxyStream:
        resolved_request = await self.resolve_message_request(payload)
        payload = resolved_request.payload
        reasoning_effort = anthropic_reasoning_effort(payload)
        provider_name = resolved_request.provider_name
        quota_key = resolved_request.quota_key

        request_id = ensure_request_id(request_id_from_headers(inbound_headers))
        started_at = time.monotonic()
        selected_account_ids: set[str] = set()
        auth_retried_account_ids: set[str] = set()
        media_type = "text/event-stream" if payload.stream else "application/json"
        affinity_quota_key = _messages_affinity_quota_key(payload, provider_name=provider_name)
        session_id = anthropic_request_session_id(payload, inbound_headers)
        useragent, useragent_group = _request_log_useragent_fields(inbound_headers)
        if provider_name == ANTHROPIC_PROVIDER_NAME:
            require_claude_code_client(useragent_group)
        sticky_key = messages_sticky_key(
            payload,
            inbound_headers,
            provider_name=provider_name,
            quota_key=affinity_quota_key,
        )
        anchor = ThreadAnchor(payload, self._repo_factory, enabled=provider_name == ANTHROPIC_PROVIDER_NAME)
        upload_class = upload_admission.upload_class(inbound_headers)
        wait_enabled = (
            bool(payload.stream)
            and provider_name == ANTHROPIC_PROVIDER_NAME
            and get_settings().anthropic_pool_exhausted_wait_enabled
        )
        first_account: Account | None = None
        first_lease: AccountLease | None = None
        initial_wait_error: AnthropicProxyError | None = None
        refuse = selection_refusal.recorder(
            self._persist_request_log,
            provider_name=provider_name,
            request_id=request_id,
            model=payload.model,
            reasoning_effort=reasoning_effort,
            started_at=started_at,
            api_key=api_key,
            session_id=session_id,
            useragent=useragent,
            useragent_group=useragent_group,
        )
        try:
            pinned_account = await anchor.pinned()
            if pinned_account is not None:
                first_account = pinned_account
            else:
                first_account, first_lease = await self._select_account(
                    payload.model,
                    provider_name=provider_name,
                    exclude_account_ids=selected_account_ids,
                    sticky_key=sticky_key,
                    quota_key=quota_key,
                    api_key=api_key,
                    lease_kind="stream",
                )
        except AnthropicProxyError as exc:
            if not wait_enabled or exc.retry_at is None:
                await refuse(exc)
                raise

            first_account = None
            first_lease = None
            initial_wait_error = exc
        lease_holder = AccountLeaseHolder(self._load_balancer, first_lease)

        async def _stream_body() -> AsyncIterator[bytes]:
            usage: AnthropicUsage | None = None
            last_account: Account | None = None
            mirror_error: AnthropicProxyError | None = None
            last_error_status: int | None = None
            last_error_message: str | None = None
            streamed_bytes = False

            last_stall: UpstreamStallTimeout | None = None
            latency_first_token_ms: int | None = None
            wait_deadline = (
                _POOL_WAIT_CLOCK() + get_settings().anthropic_pool_exhausted_wait_max_seconds if wait_enabled else None
            )
            account_override = first_account
            pending_wait_error = initial_wait_error
            while True:
                if pending_wait_error is not None:
                    await lease_holder.release()
                    await _hold_for_pool_reset(pending_wait_error, deadline=wait_deadline)
                    pending_wait_error = None
                    selected_account_ids.clear()
                    last_error_status = None
                    last_error_message = None
                    last_stall = None
                    mirror_error = None
                try:
                    wake_eligibility = await self._provider_quota_eligibility(
                        provider_name, quota_key, model=payload.model
                    )
                    max_attempts = max(_MAX_SELECTION_ATTEMPTS, len(wake_eligibility.account_ids))
                    for attempt in range(max_attempts):
                        circuit_guard: AnthropicCircuitGuard | None = None
                        keep_lease = False
                        try:
                            if account_override is not None:
                                account = account_override
                                account_override = None
                            else:
                                anchor.no_failover()
                                account, lease = await self._select_account(
                                    payload.model,
                                    provider_name=provider_name,
                                    exclude_account_ids=selected_account_ids,
                                    sticky_key=sticky_key,
                                    quota_key=quota_key,
                                    api_key=api_key,
                                    lease_kind="stream",
                                )
                                lease_holder.set(lease)
                                selected_account_ids.add(account.id)
                            if not limit_backoff.claim(account.id):
                                continue
                            last_account = anchor.serving(account)
                            last_stall = None
                            circuit_guard = AnthropicCircuitGuard(account.id, provider_name)
                            try:
                                await circuit_guard.enter()
                            except CircuitBreakerOpenError:
                                last_error_status = 503
                                last_error_message = f"Circuit breaker open for {provider_label(provider_name)} account"
                                continue
                            try:
                                access_token = await self._fresh_access_token(account)
                            except AnthropicProxyError as exc:
                                if exc.code == "mirror_token_unavailable":
                                    mirror_error = exc
                                    continue
                                if exc.status_code != 401 or not classify_refresh_error(exc.code):
                                    raise
                                await self._persist_request_log(
                                    account=account,
                                    provider_name=provider_name,
                                    request_id=request_id,
                                    model=payload.model,
                                    reasoning_effort=reasoning_effort,
                                    started_at=started_at,
                                    status="error",
                                    error_code=exc.code,
                                    error_message=exc.message,
                                    api_key=api_key,
                                    session_id=session_id,
                                    useragent=useragent,
                                    useragent_group=useragent_group,
                                )
                                last_error_status = exc.status_code
                                last_error_message = exc.message
                                continue
                            headers = build_anthropic_headers(
                                inbound_headers,
                                access_token,
                                provider_name=provider_name,
                                fast_mode=anthropic_fast_mode_requested(payload),
                            )
                            body_payload = (
                                openai_body
                                if openai_body is not None
                                else payload.model_dump(mode="json", exclude_none=True)
                            )
                            body_payload = prepare_provider_body(provider_name, payload.model, body_payload, headers)

                            unified_headers: dict[str, str] | None = None
                            quota_observed_at: datetime | None = None
                            unified_5h_utilization: float | None = None
                            unified_7d_utilization: float | None = None
                            response_metadata = AnthropicResponseMetadata()
                            cache_creation_tier = _apply_cache_tier(
                                body_payload, headers, inbound_headers, provider_name
                            )

                            if openai_body is None:
                                body_payload, _ = strip_eval_web_tools(body_payload, "anthropic")
                            if provider_name == ANTHROPIC_PROVIDER_NAME:
                                relabel_billing_block(body_payload, get_settings().anthropic_entrypoint_label)
                            raw = self._encode_json_body(body_payload)
                            nbytes = len(raw)

                            first_byte_seconds = get_settings().anthropic_first_byte_timeout_seconds
                            idle_seconds = (
                                get_settings().anthropic_stream_idle_timeout_seconds if payload.stream else 0.0
                            )
                            stall_catcher = StallCatcher()
                            async with (
                                stall_catcher,
                                admitted_http_session(account, session_id or sticky_key, nbytes, upload_class) as (
                                    session,
                                    admission_stack,
                                ),
                            ):
                                first_byte_deadline = (
                                    asyncio.get_running_loop().time() + first_byte_seconds
                                    if payload.stream and first_byte_seconds > 0
                                    else None
                                )
                                async with ConnectRetryingResponse(
                                    lambda: self._open_messages(
                                        session,
                                        provider_name=provider_name,
                                        headers=headers,
                                        json_body=body_payload,
                                        raw_bytes=raw,
                                        openai_chat=openai_body is not None,
                                    ),
                                    attempts=get_settings().upstream_connect_attempts,
                                    label=provider_label(provider_name),
                                    first_byte_deadline=first_byte_deadline,
                                    first_byte_seconds=first_byte_seconds,
                                    sleep_fn=_CONNECT_RETRY_SLEEP,
                                ) as resp:
                                    await admission_stack.aclose()
                                    quota_observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                                    unified_headers = (
                                        capture_unified_headers(resp.headers)
                                        if provider_name == ANTHROPIC_PROVIDER_NAME
                                        else None
                                    )
                                    unified_5h_utilization = None
                                    unified_7d_utilization = None
                                    if provider_name == ANTHROPIC_PROVIDER_NAME:
                                        unified_5h_utilization = _parse_unified_utilization(
                                            resp.headers.get("anthropic-ratelimit-unified-5h-utilization")
                                        )
                                        unified_7d_utilization = _parse_unified_utilization(
                                            resp.headers.get("anthropic-ratelimit-unified-7d-utilization")
                                        )
                                        await observe_anthropic_quota(account.id, resp.headers)
                                    if resp.status >= 400:
                                        raw_error = await resp.read()
                                        try:
                                            error_payload = json.loads(raw_error)
                                        except (UnicodeDecodeError, json.JSONDecodeError):
                                            error_payload = None
                                        rule = match_error_rule(
                                            provider_name,
                                            resp.status,
                                            error_payload,
                                            get_settings().upstream_error_rules,
                                        )
                                        if rule is not None:
                                            await circuit_guard.record_http_status(resp.status)
                                            await apply_error_rule(
                                                rule, account, self._repo_factory, self._load_balancer
                                            )
                                            await self._persist_request_log(
                                                account=account,
                                                provider_name=provider_name,
                                                request_id=request_id,
                                                model=payload.model,
                                                reasoning_effort=reasoning_effort,
                                                started_at=started_at,
                                                status="error",
                                                error_code=rule_error_code(rule),
                                                error_message=rule_error_message(rule),
                                                api_key=api_key,
                                                session_id=session_id,
                                                useragent=useragent,
                                                useragent_group=useragent_group,
                                                unified_5h_utilization=unified_5h_utilization,
                                                unified_7d_utilization=unified_7d_utilization,
                                                unified_headers=unified_headers,
                                                quota_observed_at=quota_observed_at,
                                            )
                                            if rule.action == "fail_request":
                                                raise AnthropicProxyError(
                                                    resp.status, rule.reason, code="upstream_rule"
                                                )
                                            last_error_status = None
                                            last_error_message = rule_client_message(
                                                rule, provider_label(provider_name)
                                            )
                                            continue
                                    if resp.status in {401, 403}:
                                        error_message = await read_error_message(resp)
                                        error_code = f"upstream_{resp.status}"
                                        if refresh_due(
                                            provider_name, resp.status, account.id, auth_retried_account_ids
                                        ):
                                            auth_retried_account_ids.add(account.id)
                                            try:
                                                await self._fresh_access_token(
                                                    account, rejected_access_token=access_token
                                                )
                                            except AnthropicProxyError as exc:
                                                error_code = exc.code or error_code
                                                error_message = exc.message
                                            else:
                                                account_override = account
                                                keep_lease = attempt + 1 < max_attempts
                                                continue
                                        if rejects_key(provider_name, resp.status, error_code):
                                            error_code = "invalid_api_key"
                                            await self._load_balancer.mark_permanent_failure(account, error_code)
                                        elif resp.status == 403 and (
                                            "oauth authentication is currently not allowed for this organization"
                                            in error_message.lower()
                                        ):
                                            async with self._repo_factory() as repos:
                                                await repos.accounts.update_status_if_current(
                                                    account.id,
                                                    account.status,
                                                    ANTHROPIC_OAUTH_ORGANIZATION_BLOCK,
                                                    account.reset_at,
                                                    blocked_at=account.blocked_at,
                                                    expected_status=account.status,
                                                    expected_deactivation_reason=account.deactivation_reason,
                                                    expected_reset_at=account.reset_at,
                                                    expected_blocked_at=account.blocked_at,
                                                )
                                        elif refuses_plan(provider_name, resp.status, error_message):
                                            error_code = "permission_error"
                                        elif error_code == "mirror_token_unavailable":
                                            mirror_error = AnthropicProxyError(503, error_message, code=error_code)
                                            continue
                                        else:
                                            await self._load_balancer.record_error(account)
                                        await self._persist_request_log(
                                            account=account,
                                            provider_name=provider_name,
                                            request_id=request_id,
                                            model=payload.model,
                                            reasoning_effort=reasoning_effort,
                                            started_at=started_at,
                                            status="error",
                                            error_code=error_code,
                                            error_message=error_message,
                                            api_key=api_key,
                                            session_id=session_id,
                                            useragent=useragent,
                                            useragent_group=useragent_group,
                                            unified_5h_utilization=unified_5h_utilization,
                                            unified_7d_utilization=unified_7d_utilization,
                                            unified_headers=unified_headers,
                                            quota_observed_at=quota_observed_at,
                                        )
                                        if error_code in STOP_STATUS:
                                            raise AnthropicProxyError(
                                                STOP_STATUS[error_code], error_message, code=error_code
                                            )
                                        last_error_status = resp.status
                                        last_error_message = error_message
                                        continue
                                    if resp.status == 429:
                                        error_message = await read_error_message(resp)
                                        rate_limit_error = rate_limit_error_from_response(resp, error_message)
                                        if provider_name == "kimi":
                                            rate_limit_error = await kimi_monthly_rate_limit_error(
                                                account, rate_limit_error, self._repo_factory
                                            )
                                        if not rate_limit_error.get("monthly_quota"):
                                            await quota_cooldown_store.record(
                                                self._repo_factory,
                                                account.id,
                                                quota_key=quota_key,
                                                error=rate_limit_error,
                                            )
                                        try:
                                            await self._load_balancer.mark_rate_limit(account, rate_limit_error)
                                        except Exception:
                                            logger.warning("Could not persist rate limit account_id=%s", account.id)
                                        await self._persist_request_log(
                                            account=account,
                                            provider_name=provider_name,
                                            request_id=request_id,
                                            model=payload.model,
                                            reasoning_effort=reasoning_effort,
                                            started_at=started_at,
                                            status="error",
                                            error_code="rate_limit_exceeded",
                                            error_message=error_message,
                                            api_key=api_key,
                                            session_id=session_id,
                                            useragent=useragent,
                                            useragent_group=useragent_group,
                                            unified_5h_utilization=unified_5h_utilization,
                                            unified_7d_utilization=unified_7d_utilization,
                                            unified_headers=unified_headers,
                                            quota_observed_at=quota_observed_at,
                                        )
                                        last_error_status = resp.status
                                        last_error_message = error_message
                                        continue
                                    if resp.status in TRANSIENT_UPSTREAM_STATUSES:
                                        error_message = await read_error_message(resp)
                                        await self._load_balancer.record_error(account)
                                        await circuit_guard.record_http_status(resp.status)
                                        await self._persist_request_log(
                                            account=account,
                                            provider_name=provider_name,
                                            request_id=request_id,
                                            model=payload.model,
                                            reasoning_effort=reasoning_effort,
                                            started_at=started_at,
                                            status="error",
                                            error_code=f"upstream_{resp.status}",
                                            error_message=error_message,
                                            api_key=api_key,
                                            session_id=session_id,
                                            useragent=useragent,
                                            useragent_group=useragent_group,
                                            unified_5h_utilization=unified_5h_utilization,
                                            unified_7d_utilization=unified_7d_utilization,
                                            unified_headers=unified_headers,
                                            quota_observed_at=quota_observed_at,
                                        )
                                        last_error_status = resp.status
                                        last_error_message = error_message
                                        if attempt + 1 < _MAX_SELECTION_ATTEMPTS:
                                            await _OVERLOADED_RETRY_SLEEP(_overloaded_backoff_seconds(attempt))
                                        elif resp.status != 529:
                                            break
                                        continue
                                    if resp.status >= 400:
                                        error_details = await read_error_details(resp)
                                        if resp.status >= 500:
                                            await self._load_balancer.record_error(account)
                                        await circuit_guard.record_http_status(resp.status)
                                        await self._persist_request_log(
                                            account=account,
                                            provider_name=provider_name,
                                            request_id=request_id,
                                            model=payload.model,
                                            reasoning_effort=reasoning_effort,
                                            started_at=started_at,
                                            status="error",
                                            error_code=f"upstream_{resp.status}",
                                            error_message=error_details.message,
                                            api_key=api_key,
                                            session_id=session_id,
                                            useragent=useragent,
                                            useragent_group=useragent_group,
                                            unified_5h_utilization=unified_5h_utilization,
                                            unified_7d_utilization=unified_7d_utilization,
                                            unified_headers=unified_headers,
                                            quota_observed_at=quota_observed_at,
                                        )
                                        raise AnthropicProxyError(
                                            resp.status,
                                            error_details.message,
                                            code=error_details.error_type or error_type_for_status(resp.status),
                                            details=error_details.details,
                                        )

                                    tripwire_error: UpstreamError | None = None
                                    if provider_name == ANTHROPIC_PROVIDER_NAME:
                                        tripwire_error = _extra_usage_tripwire_error(resp.headers)
                                    paid_fallback = account.id in wake_eligibility.paid_fallback_account_ids
                                    if (
                                        tripwire_error is not None
                                        and not paid_fallback
                                        and not get_settings().anthropic_route_to_extra_usage
                                    ):
                                        await circuit_guard.record_success()
                                        header_name = _extra_usage_tripwire_header(resp.headers) or "unknown"
                                        logger.warning(
                                            "Anthropic extra-usage response refused account_id_prefix=%s "
                                            "header=%s reset_at=%s",
                                            account.id[:8],
                                            header_name,
                                            tripwire_error.get("resets_at"),
                                        )
                                        await quota_cooldown_store.record(
                                            self._repo_factory,
                                            account.id,
                                            quota_key=_ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
                                            error=tripwire_error,
                                        )
                                        await release_upstream_response(resp)
                                        await self._persist_request_log(
                                            account=account,
                                            provider_name=provider_name,
                                            request_id=request_id,
                                            model=payload.model,
                                            reasoning_effort=reasoning_effort,
                                            started_at=started_at,
                                            status="error",
                                            error_code="extra_usage_refused",
                                            error_message="Anthropic response billed extra usage",
                                            api_key=api_key,
                                            session_id=session_id,
                                            useragent=useragent,
                                            useragent_group=useragent_group,
                                            unified_5h_utilization=unified_5h_utilization,
                                            unified_7d_utilization=unified_7d_utilization,
                                            unified_headers=unified_headers,
                                            quota_observed_at=quota_observed_at,
                                        )
                                        last_error_status = 429
                                        last_error_message = "Anthropic response billed extra usage"
                                        continue
                                    if tripwire_error is not None:
                                        logger.warning(
                                            "Anthropic response reports extra-usage billing; recording quota "
                                            "cooldown account_id=%s quota_key=%s request_id=%s",
                                            account.id,
                                            _ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
                                            request_id,
                                        )
                                        await quota_cooldown_store.record(
                                            self._repo_factory,
                                            account.id,
                                            quota_key=_ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
                                            error=tripwire_error,
                                        )

                                    text_buffer = ""
                                    stream_error: AnthropicErrorEvent | None = None

                                    raw_body = bytearray() if not payload.stream else None

                                    unsent = bytearray()
                                    scanned = 0
                                    async for chunk in stall_bounded_chunks(
                                        resp.content.iter_chunked(_STREAM_CHUNK_SIZE),
                                        first_byte_deadline=first_byte_deadline,
                                        first_byte_seconds=first_byte_seconds,
                                        idle_seconds=idle_seconds,
                                    ):
                                        if not chunk:
                                            continue
                                        chunk_bytes = bytes(chunk)
                                        if raw_body is not None:
                                            raw_body.extend(chunk_bytes)
                                        else:
                                            if openai_body is not None:
                                                text_buffer, usage, chunk_error, saw_output = collect_chat_usage(
                                                    text_buffer, chunk_bytes, usage
                                                )
                                            else:
                                                (
                                                    text_buffer,
                                                    usage,
                                                    chunk_error,
                                                    saw_output,
                                                    response_metadata,
                                                ) = _collect_response_from_chunk(
                                                    text_buffer, chunk_bytes, usage, response_metadata
                                                )
                                            if latency_first_token_ms is None and saw_output:
                                                latency_first_token_ms = int((time.monotonic() - started_at) * 1000)
                                            if chunk_error is not None:
                                                stream_error = chunk_error
                                            unsent.extend(chunk_bytes)
                                            cut = sse_event_boundary(unsent, scanned)
                                            if cut:
                                                chunk_bytes = bytes(unsent[:cut])
                                                del unsent[:cut]
                                            scanned = len(unsent)
                                            if not cut:
                                                continue
                                        streamed_bytes = True
                                        yield chunk_bytes
                                    if unsent.strip(b"\r\n"):
                                        raise UpstreamTruncatedEvent()
                                    if unsent:
                                        yield bytes(unsent)
                                    if raw_body is not None:
                                        if openai_body is not None:
                                            usage = chat_usage(json.loads(raw_body)) or usage
                                        else:
                                            parsed_usage, response_metadata = _parse_json_response(bytes(raw_body))
                                            usage = parsed_usage or usage
                                    if openai_body is None:
                                        observe_anthropic_response(
                                            request_id=request_id,
                                            account_id=account.id,
                                            provider_name=provider_name,
                                            requested_model=upstream_requested_model(body_payload, payload.model),
                                            metadata=response_metadata,
                                        )

                                    if stream_error is not None:
                                        stream_error_type = stream_error.error.type or "unknown"
                                        logger.warning(
                                            "anthropic_stream_error_event request_id=%s account_id=%s "
                                            "error_type=%s message=%s",
                                            request_id,
                                            account.id,
                                            stream_error_type,
                                            stream_error.error.message,
                                        )
                                        if stream_error_type in (
                                            "rate_limit_error",
                                            "rate_limit_exceeded",
                                            "usage_limit_reached",
                                        ):
                                            await self._load_balancer.mark_rate_limit(
                                                account, UpstreamError(message=stream_error.error.message)
                                            )
                                        elif stream_error_type in ("insufficient_quota", "quota_exceeded"):
                                            await self._load_balancer.mark_quota_exceeded(
                                                account, UpstreamError(message=stream_error.error.message)
                                            )
                                        elif is_client_stream_error(stream_error_type):
                                            await circuit_guard.record_success()
                                        else:
                                            await self._load_balancer.record_error(account)
                                        await self._persist_request_log(
                                            account=account,
                                            provider_name=provider_name,
                                            request_id=request_id,
                                            model=payload.model,
                                            reasoning_effort=reasoning_effort,
                                            started_at=started_at,
                                            status="error",
                                            error_code=f"stream_error_{stream_error_type}",
                                            error_message=stream_error.error.message,
                                            api_key=api_key,
                                            session_id=session_id,
                                            useragent=useragent,
                                            useragent_group=useragent_group,
                                            unified_5h_utilization=unified_5h_utilization,
                                            unified_7d_utilization=unified_7d_utilization,
                                            unified_headers=unified_headers,
                                            quota_observed_at=quota_observed_at,
                                            usage=usage,
                                            cache_creation_tier=cache_creation_tier,
                                            latency_first_token_ms=latency_first_token_ms,
                                            response_metadata=response_metadata,
                                        )
                                        await self._finalize_api_key_reservation(
                                            api_key_reservation,
                                            model=payload.model,
                                            usage=usage,
                                            cache_creation_tier=cache_creation_tier,
                                        )
                                        return

                                    await circuit_guard.record_success()
                                    await self._load_balancer.record_success(account)
                                    if tripwire_error is None:
                                        await quota_cooldown_store.clear(
                                            self._repo_factory, account.id, quota_key=quota_key
                                        )
                                    await self._persist_request_log(
                                        account=account,
                                        provider_name=provider_name,
                                        request_id=request_id,
                                        model=payload.model,
                                        reasoning_effort=reasoning_effort,
                                        started_at=started_at,
                                        status="success",
                                        api_key=api_key,
                                        session_id=session_id,
                                        useragent=useragent,
                                        useragent_group=useragent_group,
                                        unified_5h_utilization=unified_5h_utilization,
                                        unified_7d_utilization=unified_7d_utilization,
                                        unified_headers=unified_headers,
                                        quota_observed_at=quota_observed_at,
                                        usage=usage,
                                        cache_creation_tier=cache_creation_tier,
                                        latency_first_token_ms=latency_first_token_ms,
                                        response_metadata=response_metadata,
                                    )
                                    await self._finalize_api_key_reservation(
                                        api_key_reservation,
                                        model=payload.model,
                                        usage=usage,
                                        cache_creation_tier=cache_creation_tier,
                                    )
                                    return
                            stall = stall_catcher.stall
                            if stall is None:
                                continue

                            await circuit_guard.record_failure(stall)
                            logger.warning(
                                "anthropic_upstream_stall request_id=%s account_id=%s phase=%s bound_seconds=%g "
                                "streamed_bytes=%s",
                                request_id,
                                account.id,
                                stall.phase,
                                stall.seconds,
                                streamed_bytes,
                            )

                            await self._shielded(
                                self._record_stall(
                                    account,
                                    stall,
                                    reservation=api_key_reservation if streamed_bytes else None,
                                    provider_name=provider_name,
                                    request_id=request_id,
                                    model=payload.model,
                                    reasoning_effort=reasoning_effort,
                                    started_at=started_at,
                                    api_key=api_key,
                                    session_id=session_id,
                                    useragent=useragent,
                                    useragent_group=useragent_group,
                                    usage=usage,
                                    latency_first_token_ms=latency_first_token_ms,
                                    cache_creation_tier=cache_creation_tier,
                                    unified_headers=unified_headers,
                                    quota_observed_at=quota_observed_at,
                                    unified_5h_utilization=unified_5h_utilization,
                                    unified_7d_utilization=unified_7d_utilization,
                                    response_metadata=response_metadata,
                                )
                            )
                            if not streamed_bytes:
                                last_stall = stall
                                last_error_status = 529
                                last_error_message = str(stall)
                                continue

                            error_event = json.dumps(
                                {"type": "error", "error": {"type": "overloaded_error", "message": str(stall)}},
                                separators=(",", ":"),
                            )
                            yield f"event: error\ndata: {error_event}\n\n".encode()
                            return
                        except (aiohttp.ClientError, TimeoutError, UpstreamStallTimeout) as exc:
                            if circuit_guard is not None:
                                await circuit_guard.record_failure(exc)
                            raise
                        except AnthropicProxyError as exc:
                            if circuit_guard is not None and exc.code == "upstream_unreachable":
                                await circuit_guard.record_failure(exc)
                            raise
                        finally:
                            if circuit_guard is not None:
                                await circuit_guard.release()
                            if not keep_lease:
                                await lease_holder.release()
                except WarmupBusy:
                    continue
                except upload_admission.UploadAdmissionTimeout as exc:
                    code, message = _upload_admission_error(exc)
                    await self._persist_request_log(
                        account=last_account,
                        provider_name=provider_name,
                        request_id=request_id,
                        model=payload.model,
                        reasoning_effort=reasoning_effort,
                        started_at=started_at,
                        status="error",
                        error_code=code,
                        error_message=message,
                        api_key=api_key,
                        session_id=session_id,
                        useragent=useragent,
                        useragent_group=useragent_group,
                    )
                    raise AnthropicProxyError(503, message, code=code) from exc
                except AnthropicProxyError as exc:
                    if mirror_error is not None and last_error_message is None and pool_exhausted(exc, provider_name):
                        await self._release_api_key_reservation(api_key_reservation)
                        raise mirror_error from exc
                    if exc.code == "upstream_request_timeout":
                        raise
                    if last_stall is not None:
                        raise AnthropicProxyError(529, str(last_stall), code="overloaded_error") from exc
                    if last_error_status in TRANSIENT_UPSTREAM_STATUSES:
                        raise AnthropicProxyError(
                            last_error_status,
                            last_error_message or "Upstream unavailable",
                            code=exhausted_error_code(last_error_status, no_available_accounts_code(provider_name)),
                        ) from exc
                    if not streamed_bytes and _pool_wait_should_hold(
                        exc, wait_enabled=wait_enabled, deadline=wait_deadline
                    ):
                        pending_wait_error = exc
                        account_override = None
                        continue
                    raise

                if mirror_error is not None and last_error_message is None:
                    await self._release_api_key_reservation(api_key_reservation)
                    raise mirror_error
                if last_stall is not None:
                    await self._release_api_key_reservation(api_key_reservation)
                    raise AnthropicProxyError(529, str(last_stall), code="overloaded_error")
                message = last_error_message or f"No available {provider_label(provider_name)} accounts"
                eligibility = await self._provider_quota_eligibility(provider_name, quota_key, model=payload.model)
                exhausted_error = AnthropicProxyError(
                    last_error_status or 503,
                    message,
                    code=exhausted_error_code(last_error_status, no_available_accounts_code(provider_name)),
                    retry_at=None if last_error_status in TRANSIENT_UPSTREAM_STATUSES else eligibility.next_reset_at,
                )
                if _pool_wait_should_hold(exhausted_error, wait_enabled=wait_enabled, deadline=wait_deadline):
                    pending_wait_error = exhausted_error
                    account_override = None
                    continue
                await self._release_api_key_reservation(api_key_reservation)
                await self._persist_request_log(
                    account=last_account,
                    provider_name=provider_name,
                    request_id=request_id,
                    model=payload.model,
                    reasoning_effort=reasoning_effort,
                    started_at=started_at,
                    status="error",
                    error_code=exhausted_error.code,
                    error_message=message,
                    api_key=api_key,
                    session_id=session_id,
                    useragent=useragent,
                    useragent_group=useragent_group,
                )
                raise exhausted_error

        async def body() -> AsyncIterator[bytes]:
            stream = _stream_body()
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await close_async_iterator(stream)
                await lease_holder.release()

        try:
            if first_account is not None:
                selected_account_ids.add(first_account.id)
            raw_body = body()
            return AnthropicProxyStream(
                body=self._release_on_early_disconnect(
                    selection_refusal.watch(anchor.watch(raw_body), refuse),
                    api_key_reservation,
                    lease_holder=lease_holder,
                    close_target=raw_body,
                ),
                media_type=media_type,
            )
        except BaseException:
            await lease_holder.release()
            raise

    async def resolve_message_request(
        self,
        payload: AnthropicMessageRequest,
    ) -> AnthropicResolvedMessageRequest:
        requested_model = payload.model
        effective_model = resolve_planner_model(requested_model)
        if effective_model != requested_model:
            payload = payload.model_copy(update={"model": effective_model})
        provider_name = messages_provider_name(payload)
        quota_key = messages_quota_key(payload, provider_name=provider_name)
        return AnthropicResolvedMessageRequest(
            payload=with_route_model_identity(payload),
            provider_name=provider_name,
            quota_key=quota_key,
        )

    async def frame_request(
        self,
        method: str,
        path_and_query: str,
        body: bytes,
        inbound_headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None = None,
    ) -> AnthropicFrameResult:
        normalized_headers = {key.lower(): value for key, value in inbound_headers.items()}
        session_id = anthropic_session_header(inbound_headers) or normalized_headers.get("x-agent-lb-seat")
        useragent, useragent_group = _request_log_useragent_fields(inbound_headers)
        require_claude_code_client(useragent_group)
        account = await self._frame_account()
        started_at = time.monotonic()
        status = "error"
        error_code = "upstream_error"
        unified_headers: dict[str, str] | None = None
        quota_observed_at: datetime | None = None
        try:
            token = await self._fresh_access_token(account)
            url = URL(_upstream_base_url(ANTHROPIC_PROVIDER_NAME).rstrip("/") + path_and_query, encoded=True)
            async with lease_http_session() as session:
                for attempt in range(2):
                    headers = build_anthropic_headers(inbound_headers, token)
                    seat = normalized_headers.get("x-agent-lb-seat")
                    if seat:
                        headers["x-agent-lb-seat"] = seat
                    headers = frame_end_to_end_headers(headers)
                    async with session.request(
                        method,
                        url,
                        headers=headers,
                        data=body,
                        timeout=aiohttp.ClientTimeout(total=30),
                        allow_redirects=False,
                        auto_decompress=False,
                    ) as resp:
                        unified_headers = capture_unified_headers(resp.headers)
                        quota_observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        raw = await resp.read()
                        result = AnthropicFrameResult(resp.status, raw, frame_end_to_end_headers(resp.headers))
                    if result.status_code != 401 or attempt == 1:
                        status = "success" if result.status_code < 400 else "error"
                        error_code = None if status == "success" else f"http_{result.status_code}"
                        return result
                    token = await self._fresh_access_token(account, rejected_access_token=token)
        except AnthropicProxyError as exc:
            error_code = exc.code
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise AnthropicProxyError(502, "Anthropic frame upstream request failed", code="upstream_error") from exc
        finally:
            await self._persist_request_log(
                account=account,
                provider_name=ANTHROPIC_PROVIDER_NAME,
                request_id=ensure_request_id(request_id_from_headers(inbound_headers)),
                model="frame",
                started_at=started_at,
                status=status,
                error_code=error_code,
                api_key=api_key,
                session_id=session_id,
                useragent=useragent,
                useragent_group=useragent_group,
                unified_headers=unified_headers,
                quota_observed_at=quota_observed_at,
                unified_5h_utilization=_parse_unified_utilization(
                    (unified_headers or {}).get("anthropic-ratelimit-unified-5h-utilization")
                ),
                unified_7d_utilization=_parse_unified_utilization(
                    (unified_headers or {}).get("anthropic-ratelimit-unified-7d-utilization")
                ),
                request_kind="frame",
            )
        raise AssertionError("Frame retry loop exhausted")

    async def count_tokens(
        self,
        body: Mapping[str, Any],
        inbound_headers: Mapping[str, str],
        *,
        model: str,
        api_key: ApiKeyData | None = None,
    ) -> AnthropicCountTokensResult:
        resolved_request = await self.resolve_count_tokens_request(body, model=model)
        body = resolved_request.body
        model = resolved_request.model
        provider_name = resolved_request.provider_name
        if not get_anthropic_compat_profile(provider_name).supports_upstream_count_tokens:
            estimated = estimate_claude_input_tokens(body)
            return AnthropicCountTokensResult(
                status_code=200,
                body=json.dumps({"input_tokens": estimated}).encode(),
                media_type="application/json",
            )

        if provider_name == ANTHROPIC_PROVIDER_NAME:
            require_claude_code_client(_request_log_useragent_fields(inbound_headers)[1])
        started_at = time.monotonic()
        session_id = anthropic_session_header(inbound_headers)
        nbytes = len(json.dumps(body, separators=(",", ":")).encode())
        upload_class = upload_admission.upload_class(inbound_headers)
        excluded_account_ids: set[str] = set()
        last_result: AnthropicCountTokensResult | None = None
        last_error: AnthropicProxyError | None = None
        for _ in range(3):
            try:
                account, lease = await self._select_account(
                    model,
                    provider_name=provider_name,
                    exclude_account_ids=excluded_account_ids,
                    sticky_key=None,
                    quota_key=count_tokens_quota_key(provider_name),
                    api_key=api_key,
                    lease_kind="response_create",
                )
            except (selection_refusal.SelectionRefusal, AnthropicProxyError):
                if last_result is not None:
                    return last_result
                if last_error is not None:
                    raise last_error
                raise
            excluded_account_ids.add(account.id)
            lease_holder = AccountLeaseHolder(self._load_balancer, lease)
            circuit_guard = AnthropicCircuitGuard(account.id, provider_name)
            try:
                try:
                    await circuit_guard.enter()
                except CircuitBreakerOpenError:
                    last_error = circuit_open_error(provider_label(provider_name))
                    continue
                access_token = await self._fresh_access_token(account)
                headers = build_anthropic_headers(inbound_headers, access_token, provider_name=provider_name)
                async with admitted_http_session(account, session_id, nbytes, upload_class) as (
                    session,
                    admission_stack,
                ):
                    async with self._open_count_tokens_response(
                        session,
                        provider_name=provider_name,
                        headers=headers,
                        json_body=body,
                    ) as resp:
                        await admission_stack.aclose()
                        raw = await resp.read()
                        await circuit_guard.record_http_status(resp.status)
                        media_type = resp.headers.get("content-type") or "application/json"
                        result = AnthropicCountTokensResult(
                            status_code=resp.status, body=raw, media_type=media_type
                        )
                        if resp.status in {401, 429} or resp.status >= 500:
                            last_result = result
                            continue
                        return result
            except WarmupBusy as exc:
                raise AnthropicProxyError(503, "Warm-up concurrency is full", code="warmup_concurrency") from exc
            except upload_admission.UploadAdmissionTimeout as exc:
                code, message = _upload_admission_error(exc)
                useragent, useragent_group = _request_log_useragent_fields(inbound_headers)
                await self._persist_request_log(
                    account=account,
                    provider_name=provider_name,
                    request_id=ensure_request_id(request_id_from_headers(inbound_headers)),
                    model=model,
                    started_at=started_at,
                    status="error",
                    error_code=code,
                    error_message=message,
                    api_key=api_key,
                    session_id=session_id,
                    useragent=useragent,
                    useragent_group=useragent_group,
                )
                raise AnthropicProxyError(503, message, code=code) from exc
            except upstream_body.CompressionDeadlineExceeded as exc:
                raise compression_deadline_error() from exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                await circuit_guard.record_failure(exc)
                last_error = AnthropicProxyError(
                    502,
                    f"{provider_label(provider_name)} count_tokens upstream request failed: {exc}",
                    code="upstream_error",
                )
                continue
            finally:
                await circuit_guard.release()
                await lease_holder.release()
        if last_result is not None:
            return last_result
        if last_error is not None:
            raise last_error
        raise AnthropicProxyError(503, f"No available {provider_label(provider_name)} account", code="no_accounts")

    async def resolve_count_tokens_request(
        self,
        body: Mapping[str, Any],
        *,
        model: str,
    ) -> AnthropicResolvedCountTokensRequest:
        provider_name = provider_name_for_model(model)
        effective_model = resolve_planner_model(model)
        if effective_model != model:
            model = effective_model
            provider_name = provider_name_for_model(model)
            body = {**body, "model": model}
        return AnthropicResolvedCountTokensRequest(
            body=body,
            model=model,
            provider_name=provider_name,
        )

    async def _frame_account(self) -> Account:
        account_id = get_settings().anthropic_frame_account_id
        async with self._repo_factory() as repos:
            account = await repos.accounts.get_by_id(account_id)
            if account is not None:
                repos.accounts.session.expunge(account)
        if (
            account is None
            or account.provider != ANTHROPIC_PROVIDER_NAME
            or account.status in _FRAME_UNAVAILABLE_STATUSES
        ):
            raise AnthropicProxyError(
                503,
                f"Frame account {account_id} is unavailable; set AGENT_LB_ANTHROPIC_FRAME_ACCOUNT_ID "
                "to an active Anthropic account that owns the artifact.",
                code="frame_account_unavailable",
            )
        return account

    async def _select_account(
        self,
        model: str,
        *,
        provider_name: str,
        exclude_account_ids: set[str],
        sticky_key: str | None,
        quota_key: str,
        api_key: ApiKeyData | None = None,
        traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
        lease_kind: AccountLeaseKind | None = None,
    ) -> tuple[Account, AccountLease | None]:
        if provider_name == ANTHROPIC_PROVIDER_NAME:
            quota_key = model_quota_key(model, quota_key)
        eligibility = await self._provider_quota_eligibility(
            provider_name, quota_key, model=model, attempted_account_ids=exclude_account_ids
        )
        if provider_name == ANTHROPIC_PROVIDER_NAME and not eligibility.account_ids and eligibility.blocked_count == 0:
            async with self._repo_factory() as repos:
                has_accounts = any(
                    account.provider == provider_name for account in await repos.accounts.list_accounts()
                )
            if has_accounts:
                raise selection_refusal.SelectionRefusal(
                    429, "No selectable Anthropic account", code=no_available_accounts_code(provider_name)
                )
        if not eligibility.account_ids and eligibility.blocked_count == 0 and provider_name in {"glm", "kimi"}:
            async with self._repo_factory() as repos:
                has_no_balance = any(
                    account.provider == provider_name
                    and account.status == AccountStatus.PAUSED
                    and (account.deactivation_reason or "").startswith("balance_exhausted:")
                    for account in await repos.accounts.list_accounts()
                )
            if has_no_balance:
                raise selection_refusal.SelectionRefusal(
                    503,
                    f"No balance for {provider_label(provider_name)}; restore balance and reactivate the account.",
                    code="balance_exhausted",
                )
        if not eligibility.account_ids and eligibility.blocked_count > 0:
            reset_suffix = (
                f" Reset at {datetime.fromtimestamp(eligibility.next_reset_at, tz=timezone.utc).isoformat()}."
                if eligibility.next_reset_at is not None
                else ""
            )
            hold_suffix = (
                f" Operator holds exclude {eligibility.model_hold_count} of those accounts."
                if eligibility.model_hold_count
                else ""
            )
            weekly_suffix = (
                f" All-model weekly quota is exhausted on {eligibility.weekly_exhausted_count} of those accounts."
                if eligibility.weekly_exhausted_count
                else ""
            )
            raise selection_refusal.SelectionRefusal(
                429,
                (
                    f"All {provider_label(provider_name)} accounts are cooling down "
                    f"for quota '{quota_key}'.{reset_suffix}"
                    if eligibility.cooldown_blocked_count == eligibility.blocked_count
                    else f"No {provider_label(provider_name)} accounts are currently eligible "
                    f"for quota '{quota_key}'.{weekly_suffix}{hold_suffix}{reset_suffix}"
                ),
                code=quota_cooldown_code(provider_name),
                retry_at=eligibility.next_reset_at,
            )
        app_settings = get_settings()
        caller_policy = resolve_caller_selection_policy(api_key, traffic_class=traffic_class)
        scoped_account_ids = caller_policy.scoped_account_ids

        hold_until_exhausted = app_settings.anthropic_sticky_hold_until_exhausted
        select = partial(
            self._load_balancer.select_account,
            model=model,
            provider=provider_name,
            exclude_account_ids=exclude_account_ids,
            ignore_primary_quota_account_ids=eligibility.paid_fallback_account_ids,
            burn_first_account_ids=eligibility.burn_first_account_ids,
            burn_first_sticky_drain=bool(eligibility.burn_first_account_ids)
            and app_settings.anthropic_fable_sticky_drain_enabled
            and not hold_until_exhausted,
            sticky_key=sticky_key,
            sticky_kind=StickySessionKind.CODEX_SESSION if sticky_key else None,
            prefer_earlier_reset_accounts=True,
            prefer_earlier_reset_window="secondary",
            routing_strategy="usage_weighted",
            budget_threshold_pct=100.0 if hold_until_exhausted else 95.0,
            headroom_reallocate=app_settings.anthropic_sticky_headroom_reallocation_enabled
            and not hold_until_exhausted,
            caller_policy=caller_policy,
            lease_kind=lease_kind,
        )
        eligible_account_ids = (
            [account_id for account_id in eligibility.account_ids if account_id in scoped_account_ids]
            if scoped_account_ids is not None
            else eligibility.account_ids
        )
        preferred = (
            [account_id for account_id in eligibility.preferred_account_ids if account_id in scoped_account_ids]
            if scoped_account_ids is not None
            else eligibility.preferred_account_ids
        )
        selection = await select(account_ids=preferred if preferred else eligible_account_ids)
        if selection.account is None and preferred and len(preferred) < len(eligible_account_ids):
            selection = await select(account_ids=eligible_account_ids)
        if selection.account is None:
            labeled = f"No available {provider_label(provider_name)} accounts"
            raw_fallback = selection.error_message or labeled
            fallback = labeled if raw_fallback.startswith("No available accounts") else raw_fallback
            message, retry_at = await self._selection_failure_details(
                model=model,
                quota_key=quota_key,
                provider_name=provider_name,
                fallback=fallback,
                quota_reset_at=eligibility.next_reset_at,
                quota_blocked_count=eligibility.blocked_count,
                quota_candidate_count=len(eligibility.account_ids),
                quota_cooldown_blocked_count=eligibility.cooldown_blocked_count,
                quota_cooldown_reset_at=eligibility.cooldown_reset_at,
                model_hold_count=eligibility.model_hold_count,
                weekly_exhausted_count=eligibility.weekly_exhausted_count,
                selection_error_code=selection.error_code,
                selection_error_message=selection.error_message,
                selection_retry_at=int(selection.retry_at.timestamp()) if selection.retry_at is not None else None,
                selection_cooldown_count=sum(
                    1
                    for excluded in selection.excluded_accounts
                    if excluded.cooldown_until is not None and excluded.cooldown_until.timestamp() > time.time()
                ),
            )
            raise selection_refusal.SelectionRefusal(
                503,
                message,
                code=selection.error_code or no_available_accounts_code(provider_name),
                retry_at=retry_at,
            )
        return selection.account, selection.lease

    async def _provider_quota_eligibility(
        self,
        provider_name: str,
        quota_key: str,
        *,
        model: str | None = None,
        attempted_account_ids: Collection[str] = (),
    ) -> AnthropicQuotaEligibility:
        if provider_name == ANTHROPIC_PROVIDER_NAME:
            quota_key = model_quota_key(model, quota_key)
        if provider_name == OPENROUTER_PROVIDER_NAME:
            return await self._openrouter_credit_eligibility()
        quota_as_of = utcnow()
        now = naive_utc_to_epoch(quota_as_of)
        cache = get_quota_eligibility_cache()
        key = (provider_name, quota_key, model)
        cached = cache.get(key, now=now, attempted_account_ids=attempted_account_ids)
        if cached is not None:
            return cached
        gen = cache.generation
        settings = get_settings()
        fable_routing = provider_name == ANTHROPIC_PROVIDER_NAME and settings.anthropic_fable_routing_enabled

        extra_usage_gate = provider_name == ANTHROPIC_PROVIDER_NAME and quota_key != count_tokens_quota_key(
            provider_name
        )
        async with self._repo_factory() as repos:
            provider_accounts = [
                account for account in await repos.accounts.list_accounts() if account.provider.lower() == provider_name
            ]

            account_ids = [account.id for account in selectable_accounts(provider_accounts)]
            request_quota_cooldowns = await quota_cooldown_store.latest(
                repos.additional_usage, quota_key, account_ids, seat_wide=extra_usage_gate
            )
            extra_usage_tripwires: dict[str, AdditionalUsageHistory] = {}
            if extra_usage_gate:
                extra_usage_tripwires = await repos.additional_usage.latest_by_account(
                    _ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
                    quota_cooldown_store.COOLDOWN_WINDOW,
                    account_ids=account_ids,
                )

            primary_usage: dict[str, UsageHistory] = {}
            primary_exhaustion: dict[str, int] = {}
            primary_headroom_recorded_at: dict[str, int] = {}
            credit_enabled_exhausted_account_ids: frozenset[str] = frozenset()
            if extra_usage_gate:
                primary_usage = await repos.usage.latest_by_account(window="primary", account_ids=account_ids)
                primary_headroom_recorded_at = {
                    account_id: naive_utc_to_epoch(entry.recorded_at)
                    for account_id, entry in primary_usage.items()
                    if entry.used_percent is not None and float(entry.used_percent) < 100.0
                }
                primary_exhaustion = {
                    account_id: int(entry.reset_at)
                    for account_id, entry in primary_usage.items()
                    if entry.used_percent is not None
                    and float(entry.used_percent) >= 100.0
                    and entry.reset_at is not None
                    and int(entry.reset_at) > now
                }
                credit_enabled_exhausted_account_ids = frozenset(
                    account_id
                    for account_id, entry in primary_usage.items()
                    if account_id in primary_exhaustion
                    and (
                        entry.credits_unlimited is True
                        or (
                            entry.credits_has is True
                            and entry.credits_balance is not None
                            and float(entry.credits_balance) > 0.0
                        )
                    )
                )
            weekly_usage_used_percent: dict[str, float] = {}
            secondary_exhaustion: dict[str, int | None] = {}
            secondary_usage: dict[str, UsageHistory] = {}
            if extra_usage_gate or fable_routing:
                secondary_usage = await repos.usage.latest_by_account(window="secondary", account_ids=account_ids)
                weekly_usage_used_percent = {
                    account_id: float(entry.used_percent) for account_id, entry in secondary_usage.items()
                }
                secondary_exhaustion = {
                    account_id: int(entry.reset_at) if entry.reset_at is not None else None
                    for account_id, entry in secondary_usage.items()
                    if entry.used_percent is not None
                    and float(entry.used_percent) >= 100.0
                    and (entry.reset_at is None or int(entry.reset_at) > now)
                }
            extra_usage_blocks = {
                account_id: tripwire.reset_at
                for account_id, tripwire in extra_usage_tripwires.items()
                if extra_usage_blocked(
                    tripwire,
                    primary_usage.get(account_id),
                    secondary_usage.get(account_id),
                    now=quota_as_of,
                    route_paid_extra=settings.anthropic_route_to_extra_usage,
                )
            }
            fable_access_markers: dict[str, tuple[float, int | None]] = {}
            fable_scoped_markers: dict[str, tuple[float, int | None, int]] = {}
            if fable_routing:
                fable_access = await repos.additional_usage.latest_by_account(
                    _ANTHROPIC_FABLE_ACCESS_QUOTA_KEY,
                    _ANTHROPIC_FABLE_ACCESS_WINDOW,
                    account_ids=account_ids,
                )
                fable_access_markers = {
                    account_id: (float(entry.used_percent), entry.reset_at)
                    for account_id, entry in fable_access.items()
                }
                fable_scoped = await repos.additional_usage.latest_by_account(
                    _ANTHROPIC_FABLE_SCOPED_WEEKLY_QUOTA_KEY,
                    _ANTHROPIC_FABLE_SCOPED_WEEKLY_WINDOW,
                    account_ids=account_ids,
                )

                fable_scoped_markers = {
                    account_id: (
                        float(entry.used_percent),
                        entry.reset_at,
                        naive_utc_to_epoch(entry.recorded_at),
                    )
                    for account_id, entry in fable_scoped.items()
                }

        fable_request = fable_routing and is_fable_model(model)
        model_holds = active_model_exclusions(resolve_model_alias(model), now) if fable_request else {}
        threshold = settings.anthropic_fable_weekly_max_used_percent if fable_routing else 0.0
        scoped_threshold = settings.anthropic_fable_scoped_max_used_percent if fable_routing else 0.0

        def _weekly_used(account_id: str) -> float:
            return weekly_usage_used_percent.get(account_id, 0.0)

        def _fresh_scoped_percent(account_id: str) -> float | None:

            marker = fable_scoped_markers.get(account_id)
            if marker is None:
                return None
            scoped_used_percent, _scoped_reset_at, recorded_epoch = marker
            if now - recorded_epoch >= _FABLE_SCOPED_FRESH_SECONDS:
                return None
            return scoped_used_percent

        def _has_fresh_capable_fable_marker(account_id: str) -> bool:
            marker = fable_access_markers.get(account_id)
            if marker is None:
                return False
            marker_used_percent, marker_reset_at = marker
            if marker_used_percent >= 100.0:
                return False
            return marker_reset_at is None or marker_reset_at > now

        model_scope_account_ids = account_ids
        preferred_fable_account_ids: frozenset[str] = frozenset()
        if fable_request:
            model_scope_account_ids = []
            preferred_ids: set[str] = set()
            for account_id in account_ids:
                scoped_percent = _fresh_scoped_percent(account_id)

                model_scope_account_ids.append(account_id)
                if (
                    scoped_percent is not None
                    or _weekly_used(account_id) < threshold
                    or _has_fresh_capable_fable_marker(account_id)
                ):
                    preferred_ids.add(account_id)
            preferred_fable_account_ids = frozenset(preferred_ids)

        eligible_ids: list[str] = []
        blocked_reset_by_account_id: dict[str, int] = {}
        blocked_count = 0
        model_hold_count = 0
        weekly_exhausted_count = 0
        request_quota_blocked_account_ids: set[str] = set()

        route_paid_extra = settings.anthropic_route_to_extra_usage

        for account_id in model_scope_account_ids:
            if account_id in model_holds:
                model_hold_count += 1
                blocked_count += 1
                blocked_reset_by_account_id[account_id] = int(model_holds[account_id])
                continue

            held = limit_backoff.hold_until(
                account_id, request_quota_cooldowns.get(account_id), now, primary_headroom_recorded_at.get(account_id)
            )
            if held is not None:
                blocked_count += 1
                request_quota_blocked_account_ids.add(account_id)
                blocked_reset_by_account_id[account_id] = held
                continue

            if extra_usage_gate and not route_paid_extra and account_id in secondary_exhaustion:
                blocked_count += 1
                if fable_request:
                    weekly_exhausted_count += 1
                weekly_reset = secondary_exhaustion[account_id]
                if weekly_reset is not None:
                    blocked_reset_by_account_id[account_id] = int(weekly_reset)
                continue
            tripwire_reset = extra_usage_blocks.get(account_id)
            if tripwire_reset is not None:
                blocked_count += 1
                blocked_reset_by_account_id[account_id] = tripwire_reset
                continue
            eligible_ids.append(account_id)

        pool_primary_exhausted = bool(model_scope_account_ids) and all(
            account_id in primary_exhaustion for account_id in model_scope_account_ids
        )
        paid_fallback_account_ids: frozenset[str] = frozenset()
        if not eligible_ids and pool_primary_exhausted and settings.anthropic_route_to_extra_usage:
            paid_candidates = [
                account_id
                for account_id in model_scope_account_ids
                if account_id in credit_enabled_exhausted_account_ids
                and account_id not in model_holds
                and account_id not in request_quota_blocked_account_ids
                and account_id not in secondary_exhaustion
            ]
            eligible_ids = paid_candidates
            paid_fallback_account_ids = frozenset(paid_candidates)
            blocked_count -= len(eligible_ids)
            for account_id in eligible_ids:
                blocked_reset_by_account_id.pop(account_id, None)

        burn_first_account_ids: frozenset[str] = frozenset()

        if fable_routing and settings.anthropic_fable_burn_first_enabled and not fable_request and eligible_ids:

            def _is_over_fable_threshold(account_id: str) -> bool:
                scoped_percent = _fresh_scoped_percent(account_id)
                if scoped_percent is not None:
                    return scoped_percent >= scoped_threshold
                return _weekly_used(account_id) >= threshold

            burn_first_account_ids = frozenset(
                account_id for account_id in eligible_ids if _is_over_fable_threshold(account_id)
            )

        cooling_account_ids = request_quota_blocked_account_ids - set(eligible_ids)
        cooldown_resets = [
            blocked_reset_by_account_id[account_id]
            for account_id in cooling_account_ids
            if account_id in blocked_reset_by_account_id
        ]
        return cache.store_and_filter(
            key,
            AnthropicQuotaEligibility(
                account_ids=eligible_ids,
                preferred_account_ids=tuple(
                    account_id for account_id in eligible_ids if account_id in preferred_fable_account_ids
                ),
                blocked_count=blocked_count,
                next_reset_at=min(blocked_reset_by_account_id.values()) if blocked_reset_by_account_id else None,
                cooldown_blocked_count=len(cooling_account_ids),
                cooldown_reset_at=min(cooldown_resets) if cooldown_resets else None,
                model_hold_count=model_hold_count,
                weekly_exhausted_count=weekly_exhausted_count,
                paid_fallback_account_ids=paid_fallback_account_ids,
                burn_first_account_ids=burn_first_account_ids,
            ),
            gen,
            attempted_account_ids,
        )

    async def _persist_request_log(
        self,
        *,
        account: Account | None,
        provider_name: str,
        request_id: str,
        model: str,
        reasoning_effort: str | None = None,
        started_at: float,
        status: str,
        api_key: ApiKeyData | None,
        session_id: str | None,
        useragent: str | None,
        useragent_group: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: AnthropicUsage | None = None,
        cache_creation_tier: str | None = None,
        latency_first_token_ms: int | None = None,
        unified_5h_utilization: float | None = None,
        unified_7d_utilization: float | None = None,
        unified_headers: dict[str, str] | None = None,
        quota_observed_at: datetime | None = None,
        request_kind: str = "normal",
        response_metadata: AnthropicResponseMetadata | None = None,
    ) -> None:

        task = asyncio.create_task(
            self._persist_request_log_row(
                account=account,
                provider_name=provider_name,
                request_id=request_id,
                model=model,
                reasoning_effort=reasoning_effort,
                started_at=started_at,
                status=status,
                api_key=api_key,
                session_id=session_id,
                useragent=useragent,
                useragent_group=useragent_group,
                error_code=error_code,
                error_message=error_message,
                usage=usage,
                cache_creation_tier=cache_creation_tier,
                latency_first_token_ms=latency_first_token_ms,
                identity=get_request_identity(),
                unified_5h_utilization=unified_5h_utilization,
                unified_7d_utilization=unified_7d_utilization,
                unified_headers=unified_headers,
                quota_observed_at=quota_observed_at,
                request_kind=request_kind,
                response_metadata=response_metadata,
            )
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            self._pending_request_logs.add(task)
            task.add_done_callback(self._request_log_finished)
            raise

    def _request_log_finished(self, task: asyncio.Task[None]) -> None:
        self._pending_request_logs.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("Anthropic request log task cancelled")
        except Exception:
            logger.warning("Anthropic request log task failed", exc_info=True)

    async def _persist_request_log_row(
        self,
        *,
        account: Account | None,
        provider_name: str,
        request_id: str,
        model: str,
        reasoning_effort: str | None = None,
        started_at: float,
        status: str,
        api_key: ApiKeyData | None,
        session_id: str | None,
        useragent: str | None,
        useragent_group: str | None,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: AnthropicUsage | None = None,
        cache_creation_tier: str | None = None,
        latency_first_token_ms: int | None = None,
        unified_5h_utilization: float | None = None,
        unified_7d_utilization: float | None = None,
        unified_headers: dict[str, str] | None = None,
        quota_observed_at: datetime | None = None,
        identity: RequestIdentity | None = None,
        request_kind: str = "normal",
        response_metadata: AnthropicResponseMetadata | None = None,
    ) -> None:
        try:
            dispatch_id = get_dispatch_id()
            if dispatch_id:
                unified_headers = {**(unified_headers or {}), "x-agent-lb-dispatch": dispatch_id}
            latency_ms = int((time.monotonic() - started_at) * 1000)
            identity = await attach_member(identity, api_key.member_id if api_key else None)
            async with self._repo_factory() as repos:
                await repos.request_logs.add_log(
                    identity=identity,
                    account_id=account.id if account else None,
                    api_key_id=api_key.id if api_key else None,
                    request_id=request_id,
                    model=model,
                    response_model=response_metadata.response_model if response_metadata else None,
                    stop_reason=response_metadata.stop_reason if response_metadata else None,
                    stop_details_category=response_metadata.stop_details_category if response_metadata else None,
                    reasoning_effort=reasoning_effort,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    cached_input_tokens=usage.cache_read_input_tokens if usage else None,
                    cache_creation_tokens=usage.cache_creation_input_tokens if usage else None,
                    cache_creation_tier=cache_creation_tier,
                    cache_read_tokens=usage.cache_read_input_tokens if usage else None,
                    latency_ms=latency_ms,
                    latency_first_token_ms=latency_first_token_ms,
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    plan_type=account.plan_type if account else None,
                    provider=provider_name,
                    upstream_cost_usd=usage.cost if usage else None,
                    unified_5h_utilization=unified_5h_utilization,
                    unified_7d_utilization=unified_7d_utilization,
                    unified_headers=unified_headers,
                    quota_observed_at=quota_observed_at,
                    transport="http",
                    session_id=session_id,
                    useragent=useragent,
                    useragent_group=useragent_group,
                    request_kind=request_kind,
                )
        except Exception:
            logger.warning("Failed to persist Anthropic request log request_id=%s", request_id, exc_info=True)

    async def _finalize_api_key_reservation(
        self,
        reservation: ApiKeyUsageReservationData | None,
        *,
        model: str,
        usage: AnthropicUsage | None,
        cache_creation_tier: str | None = None,
    ) -> None:
        if reservation is None:
            return
        if usage is None:
            await self._release_api_key_reservation(reservation)
            return
        async with self._repo_factory() as repos:
            service = ApiKeysService(repos.api_keys)
            cached_tokens = (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cached_input_tokens=cached_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_tier=cache_creation_tier,
                cache_creation_1h_input_tokens=cache_creation_1h_tokens(usage),
            )

    async def _record_stall(
        self,
        account: Account,
        stall: UpstreamStallTimeout,
        *,
        reservation: ApiKeyUsageReservationData | None,
        provider_name: str,
        request_id: str,
        model: str,
        reasoning_effort: str | None = None,
        started_at: float,
        api_key: ApiKeyData | None,
        session_id: str | None,
        useragent: str | None,
        useragent_group: str | None,
        usage: AnthropicUsage | None,
        latency_first_token_ms: int | None,
        cache_creation_tier: str | None = None,
        unified_headers: dict[str, str] | None = None,
        quota_observed_at: datetime | None = None,
        unified_5h_utilization: float | None = None,
        unified_7d_utilization: float | None = None,
        response_metadata: AnthropicResponseMetadata | None = None,
    ) -> None:
        await self._finalize_api_key_reservation(
            reservation, model=model, usage=usage, cache_creation_tier=cache_creation_tier
        )
        await self._load_balancer.record_error(account)
        await self._persist_request_log(
            account=account,
            provider_name=provider_name,
            request_id=request_id,
            model=model,
            reasoning_effort=reasoning_effort,
            started_at=started_at,
            status="error",
            error_code=stall.code,
            error_message=str(stall),
            api_key=api_key,
            session_id=session_id,
            useragent=useragent,
            useragent_group=useragent_group,
            unified_headers=unified_headers,
            quota_observed_at=quota_observed_at,
            unified_5h_utilization=unified_5h_utilization,
            unified_7d_utilization=unified_7d_utilization,
            usage=usage,
            latency_first_token_ms=latency_first_token_ms,
            response_metadata=response_metadata,
        )

    async def _release_on_early_disconnect(
        self,
        body: AsyncIterator[bytes],
        reservation: ApiKeyUsageReservationData | None,
        lease_holder: AccountLeaseHolder | None = None,
        close_target: AsyncIterator[bytes] | None = None,
    ) -> AsyncIterator[bytes]:
        forwarded = False
        try:
            async for chunk in body:
                forwarded = True
                yield chunk
        except asyncio.CancelledError:
            if not forwarded and reservation is not None:
                await self._shielded(self._release_api_key_reservation(reservation))
            raise
        finally:
            await close_async_iterator(body)
            await close_async_iterator(close_target)
            if lease_holder is not None:
                await lease_holder.release()

    async def _shielded(self, work: Coroutine[Any, Any, None]) -> None:

        task = asyncio.create_task(work)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            self._pending_bookkeeping.add(task)
            task.add_done_callback(self._bookkeeping_finished)
            raise

    def _bookkeeping_finished(self, task: asyncio.Task[None]) -> None:
        self._pending_bookkeeping.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("Anthropic bookkeeping task cancelled")
        except Exception:
            logger.warning("Anthropic bookkeeping task failed", exc_info=True)

    async def _release_api_key_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None:
            return
        async with self._repo_factory() as repos:
            service = ApiKeysService(repos.api_keys)
            await service.release_usage_reservation(reservation.reservation_id)

    async def _openrouter_credit_eligibility(self) -> AnthropicQuotaEligibility:
        async with self._repo_factory() as repos:
            provider_accounts = [
                account
                for account in await repos.accounts.list_accounts()
                if account.provider.lower() == OPENROUTER_PROVIDER_NAME
            ]
            selectable = selectable_accounts(provider_accounts)
            account_ids = [account.id for account in selectable]
            credits_usage = await repos.usage.latest_by_account(
                window=CREDITS_USAGE_WINDOW,
                account_ids=account_ids,
            )
            eligible: list[str] = []
            blocked = 0
            for account in selectable:
                if account.status == AccountStatus.QUOTA_EXCEEDED:
                    blocked += 1
                    continue
                window = window_from_usage(credits_usage.get(account.id))
                if window is not None and credits_exhausted(window):
                    blocked += 1
                    continue
                eligible.append(account.id)
            return AnthropicQuotaEligibility(account_ids=eligible, blocked_count=blocked)

    async def claim_session_route(
        self,
        *,
        session_id: str,
        model: str,
        quota_key: str,
        affinity_quota_key: str | None = None,
        provider_name: str = ANTHROPIC_PROVIDER_NAME,
        api_key: ApiKeyData | None = None,
    ) -> Account:
        sticky_quota_key = affinity_quota_key or quota_key
        sticky_key = f"{sticky_prefix(provider_name)}:{sticky_quota_key}:session:{hash_for_key(session_id)}"
        account, _ = await self._select_account(
            model,
            provider_name=provider_name,
            exclude_account_ids=set(),
            sticky_key=sticky_key,
            quota_key=quota_key,
            api_key=api_key,
            lease_kind=None,
        )
        async with self._repo_factory() as repos:
            await repos.sticky_sessions.upsert(sticky_key, account.id, kind=StickySessionKind.CODEX_SESSION)
        return account


def _upstream_base_url(provider_name: str) -> str:
    profile = get_anthropic_compat_profile(provider_name)
    if profile.upstream_base_url is not None:
        return profile.upstream_base_url
    assert profile.upstream_settings_attr is not None
    return str(getattr(get_settings(), profile.upstream_settings_attr))


def _pool_wait_should_hold(
    exc: AnthropicProxyError,
    *,
    wait_enabled: bool,
    deadline: float | None,
) -> bool:
    if not wait_enabled or deadline is None:
        return False

    if exc.retry_at is None:
        return False
    return _POOL_WAIT_CLOCK() < deadline


async def _hold_for_pool_reset(error: AnthropicProxyError, *, deadline: float | None) -> None:
    if deadline is None:
        raise error
    now = _POOL_WAIT_CLOCK()
    remaining = deadline - now
    if remaining <= 0:
        raise error
    delay = _pool_wait_delay_seconds(error.retry_at, now=now, remaining=remaining)
    logger.info(
        "Anthropic pool exhausted; holding stream open for reset retry_at=%s delay_seconds=%.1f",
        error.retry_at,
        delay,
    )
    if delay > 0:
        await _POOL_WAIT_SLEEP(delay)


def _pool_wait_delay_seconds(retry_at: int | None, *, now: float, remaining: float) -> float:
    base = float(retry_at) - now if retry_at is not None else _POOL_WAIT_MIN_POLL_SECONDS
    base = min(max(base, _POOL_WAIT_MIN_POLL_SECONDS), _POOL_WAIT_MAX_POLL_SECONDS)
    return max(0.0, min(base + _POOL_WAIT_JITTER(), remaining))


def _anthropic_sticky_key(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
    *,
    quota_key: str,
) -> str | None:
    header_value = anthropic_session_header(headers)
    if header_value:
        return f"claude:{quota_key}:session:{hash_for_key(header_value)}"
    derived = derive_anthropic_session_material(payload)
    if not derived:
        return None
    return f"claude:{quota_key}:derived:{hash_for_key(derived)}"
