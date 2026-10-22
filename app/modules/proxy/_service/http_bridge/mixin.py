from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, overload
from uuid import uuid4

import aiohttp
import anyio

from app.core import shutdown as shutdown_state
from app.core.auth.refresh import (
    RefreshError,
)
from app.core.clients.files import create_file as core_create_file  # noqa: F401
from app.core.clients.files import finalize_file as core_finalize_file  # noqa: F401
from app.core.clients.proxy import CodexControlResponse as CodexControlResponse
from app.core.clients.proxy import (  # noqa: F401  # noqa: F401
    ImageFetchSession,
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    _as_image_fetch_session,
    _inline_content_images,
    _inline_input_image_urls,
    _ws_transport_payload_budget_bytes,
    filter_inbound_headers,
    pop_compact_timeout_overrides,
    pop_stream_timeout_overrides,
    pop_transcribe_timeout_overrides,
    push_compact_timeout_overrides,
    push_stream_timeout_overrides,
    push_transcribe_timeout_overrides,
)
from app.core.clients.proxy import codex_control_request as core_codex_control_request  # noqa: F401
from app.core.clients.proxy import compact_responses as core_compact_responses  # noqa: F401
from app.core.clients.proxy import transcribe_audio as core_transcribe_audio  # noqa: F401
from app.core.config.settings import Settings
from app.core.errors import (
    openai_error,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_instance_mismatch_total,
    bridge_local_rebind_total,
    bridge_owner_mismatch_total,
    bridge_prompt_cache_locality_miss_total,
    bridge_soft_local_rebind_total,
)
from app.core.resilience.overload import local_overload_error
from app.db.models import (
    AccountStatus,
    StickySessionKind,
)
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyRequestUsageBudget
from app.modules.proxy._service.api_key_usage import (
    _API_KEY_RESERVATION_HEARTBEAT_SECONDS as _API_KEY_RESERVATION_HEARTBEAT_SECONDS,
)
from app.modules.proxy._service.compact import (
    _sticky_key_for_compact_request as _sticky_key_for_compact_request,
)
from app.modules.proxy._service.compact import (
    _sticky_key_from_compact_payload as _sticky_key_from_compact_payload,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _active_http_bridge_instance_ring,
    _durable_bridge_lookup_active_owner,
    _durable_bridge_lookup_allows_local_reuse,
    _forwarded_http_bridge_session_key,
    _http_bridge_allow_durable_takeover,
    _http_bridge_can_local_recover_without_ring,
    _http_bridge_can_recover_during_drain,
    _http_bridge_can_single_instance_owner_takeover_without_anchor,
    _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor,
    _http_bridge_continuity_lost_error_envelope,
    _http_bridge_durable_lease_ttl_seconds,
    _http_bridge_endpoint_matches_current_instance,
    _http_bridge_eviction_priority,
    _http_bridge_has_durable_recovery_anchor,
    _http_bridge_key_strength,
    _http_bridge_owner_check_required,
    _http_bridge_owner_instance,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _http_bridge_previous_response_alias_key,
    _http_bridge_request_counts_against_queue,
    _http_bridge_session_allows_api_key,
    _http_bridge_session_matches_preferred_account,
    _http_bridge_session_retiring_with_visible_requests,
    _http_bridge_session_reusable_for_request,
    _http_bridge_should_wait_for_registration,
    _http_bridge_startup_wait_timeout_error,
    _http_bridge_turn_state_alias_key,
    _is_missing_durable_bridge_table_error,
    _log_http_bridge_event,
    _log_http_bridge_startup_wait_timeout,
    _preferred_http_bridge_reconnect_turn_state,
    _record_bridge_drain_recovery_allowed,
    _record_bridge_first_turn_timeout,
    _record_bridge_reattach,
)
from app.modules.proxy._service.http_bridge.owner_forwarding import _HTTPBridgeOwnerForwardingMixin
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeRequestSubmitMixin
from app.modules.proxy._service.http_bridge.service_stubs import (
    _await_cancelled_task,
    _call_with_supported_optional_kwargs,
    _estimated_lease_tokens_from_request_usage_budget,
    _headers_with_turn_state,
    _is_local_account_cap_code,
    _prefer_earlier_reset_window,
    _proxy_admission_wait_timeout_seconds,
    _raise_proxy_unavailable,
    _record_continuity_fail_closed,
    _record_same_account_takeover,
    _remaining_budget_seconds,
    _routing_strategy,
    _service_get_settings,
    _service_get_settings_cache,
    _service_time,
    _upstream_turn_state_from_socket,
    _websocket_connect_deadline,
)
from app.modules.proxy._service.http_bridge.session_registry import _HTTPBridgeSessionRegistryMixin
from app.modules.proxy._service.http_bridge.streaming import _HTTPBridgeStreamingMixin
from app.modules.proxy._service.http_bridge.upstream_events import _HTTPBridgeUpstreamEventsMixin
from app.modules.proxy._service.observability import (
    _hash_identifier as _hash_identifier,
)
from app.modules.proxy._service.observability import (
    _hash_identifier_or_none as _hash_identifier_or_none,
)
from app.modules.proxy._service.observability import (
    _interesting_header_keys as _interesting_header_keys,
)
from app.modules.proxy._service.observability import (
    _tools_hash as _tools_hash,
)
from app.modules.proxy._service.observability import (
    _truncate_identifier as _truncate_identifier,
)
from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS,  # noqa: F401
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,  # noqa: F401
    _copy_websocket_route_metadata_to_session,
    _HTTPBridgeOwnerForward,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _WebSocketRequestState,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.support import (
    _websocket_route_log_kwargs as _websocket_route_log_kwargs,
)
from app.modules.proxy._service.warmup import (
    WarmupExecutionData as WarmupExecutionData,
)
from app.modules.proxy._service.warmup import (
    WarmupFailedAccountData as WarmupFailedAccountData,
)
from app.modules.proxy._service.warmup import (
    WarmupSkippedAccountData as WarmupSkippedAccountData,
)
from app.modules.proxy._service.warmup import (
    WarmupSubmittedAccountData as WarmupSubmittedAccountData,
)
from app.modules.proxy._service.warmup import (
    _is_warmup_usage_eligible as _is_warmup_usage_eligible,
)
from app.modules.proxy._service.warmup import (
    _materialize_warmup_account as _materialize_warmup_account,
)
from app.modules.proxy._service.warmup import (
    _snapshot_warmup_account as _snapshot_warmup_account,
)
from app.modules.proxy._service.warmup import (
    _WarmupAccountSnapshot as _WarmupAccountSnapshot,
)
from app.modules.proxy._service.warmup import (
    _WarmupSubmitResult as _WarmupSubmitResult,
)
from app.modules.proxy._service.warmup import (
    _WarmupUsageSnapshot as _WarmupUsageSnapshot,
)
from app.modules.proxy.affinity import (
    _AffinityPolicy,
    _extract_model_class,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)
from app.modules.proxy.durable_bridge_coordinator import (
    DurableBridgeLookup,
)
from app.modules.proxy.load_balancer import AccountLease
from app.modules.proxy.selection_diagnostics import enrich_selection_error

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
TEXT_DELTA_EVENT_TYPES = frozenset({"response.output_text.delta", "response.refusal.delta"})
REQUEST_TRANSPORT_HTTP = "http"
UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY = frozenset({1011})
WEBSOCKET_AUTH_INVALIDATED_FAILURE_CODE = "account_auth_invalidated"
SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE = "security_work_authorization_required"
NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE = "no_security_work_authorized_accounts"
SECURITY_WORK_RETRY_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work. "
    "agent-lb is retrying on an account marked as authorized for security work."
)
SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work, but no account is marked as authorized for "
    "security work. agent-lb is continuing with normal account selection; the upstream request may still fail until "
    "an account with Trusted Access for Cyber is marked as security-work-authorized."
)
HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS = 5.0
HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD = 100
HTTP_BRIDGE_IDLE_PRUNE_MIN_INTERVAL_SECONDS = 5.0


@dataclass
class BridgeSessionAcquisition:
    affinity: Any = None
    alias_index_key: Any = None
    alias_key: Any = None
    alias_session: Any = None
    allow_bootstrap_owner_rebind: Any = None
    allow_forward_to_owner: Any = None
    allow_previous_response_recovery_rebind: Any = None
    api_key: Any = None
    api_key_id: Any = None
    candidate_key: Any = None
    candidate_session: Any = None
    capacity_wait_future: Any = None
    continuity_error: Any = None
    create_accepts_var_keyword: Any = None
    create_kwargs: Any = None
    create_session: Any = None
    create_signature: Any = None
    created_session: Any = None
    current_future: Any = None
    current_instance: Any = None
    detached: Any = None
    durable_lookup: Any = None
    effective_idle_ttl_seconds: Any = None
    evictable_sessions: Any = None
    existing: Any = None
    fallback_on_preferred_account_unavailable: Any = None
    force_durable_takeover: Any = None
    force_durable_takeover_after_detach: Any = None
    forwarded_affinity: Any = None
    forwarded_affinity_key: Any = None
    forwarded_affinity_kind: Any = None
    forwarded_request: Any = None
    gateway_safe_mode: Any = None
    hard_continuity_lookup: Any = None
    headers: Any = None
    idle_ttl_seconds: Any = None
    incoming_session_key: Any = None
    incoming_turn_state: Any = None
    inflight_future: Any = None
    key: Any = None
    lru_key: Any = None
    lru_session: Any = None
    max_sessions: Any = None
    missing_turn_state_alias: Any = None
    old_account_id: Any = None
    owner_check_required: Any = None
    owner_endpoint: Any = None
    owner_forward: Any = None
    owner_instance: Any = None
    owner_mismatch: Any = None
    owner_mismatch_error: Any = None
    owns_creation: Any = None
    pending_count: Any = None
    preferred_account_id: Any = None
    preserve_durable_canonical_key: Any = None
    previous_alias_key: Any = None
    previous_key: Any = None
    previous_response_id: Any = None
    previous_session: Any = None
    pruned_sessions: Any = None
    registered: Any = None
    request_model: Any = None
    request_stage: Any = None
    request_usage_budget: Any = None
    require_preferred_account: Any = None
    response_create_concurrency: Any = None
    retiring_with_visible_requests: Any = None
    ring: Any = None
    ring_lookup_failed: Any = None
    session_registered: Any = None
    session_to_close: Any = None
    session_to_return_after_close: Any = None
    sessions_to_close_before_create: Any = None
    settings: Any = None
    skip_registration_gate: Any = None
    stale_key: Any = None
    timeout_error: Any = None
    turn_state_scope_conflict: Any = None
    used_session_header_fallback: Any = None
    wait_timeout_seconds: Any = None


class BridgeOperationsProtocol(_HTTPBridgeServiceProtocol):
    _background_cleanup_tasks: Any
    _claim_durable_http_bridge_session: Any
    _close_http_bridge_session: Any
    _close_http_bridge_session_bounded: Any
    _create_http_bridge_session: Any
    _detach_http_bridge_session_locked: Any
    _drain_http_bridge_background_cleanup_tasks: Any
    _durable_bridge: Any
    _ensure_fresh_with_budget: Any
    _fail_pending_websocket_requests: Any
    _handle_proxy_error: Any
    _http_bridge_account_cooldowns: Any
    _http_bridge_account_still_servable: Any
    _http_bridge_inflight_sessions: Any
    _http_bridge_last_idle_prune_monotonic: Any
    _http_bridge_lock: Any
    _http_bridge_pending_count_nowait: Any
    _http_bridge_previous_response_index: Any
    _http_bridge_sessions: Any
    _http_bridge_turn_state_index: Any
    _load_balancer: Any
    _open_upstream_websocket_with_budget: Any
    _promote_http_bridge_session_to_codex_affinity: Any
    _prune_http_bridge_sessions_locked: Any
    _refresh_durable_http_bridge_session: Any
    _relay_http_bridge_upstream_messages: Any
    _repo_factory: Any
    _ring_membership: Any
    _schedule_cancel_safe_cleanup: Any
    _schedule_http_bridge_session_closes: Any
    _select_account_with_budget_for_stream: Any
    _unregister_http_bridge_previous_response_ids: Any
    _unregister_http_bridge_previous_response_ids_locked: Any
    _unregister_http_bridge_turn_states: Any
    _unregister_http_bridge_turn_states_locked: Any
    resolve_bridge_owner_forward: Any


class BridgeLifecycleMixin(BridgeOperationsProtocol):
    async def _http_bridge_pending_count(self, session: "_HTTPBridgeSession") -> int:
        async with session.pending_lock:
            visible_pending_count = sum(
                1
                for request_state in session.pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            )
            return max(visible_pending_count, session.queued_request_count)

    def _http_bridge_pending_count_nowait(
        self,
        session: "_HTTPBridgeSession",
        *,
        context: str,
    ) -> int | None:
        try:
            session.pending_lock.acquire_nowait()
        except (anyio.WouldBlock, RuntimeError):
            logger.warning(
                "http_bridge_pending_count_unavailable context=%s bridge_kind=%s bridge_key=%s account_id=%s model=%s",
                context,
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                session.account.id,
                session.request_model,
            )
            return None
        try:
            visible_pending_count = sum(
                1
                for request_state in session.pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            )
            return max(visible_pending_count, session.queued_request_count)
        finally:
            session.pending_lock.release()

    async def _close_http_bridge_session_bounded(
        self,
        session: "_HTTPBridgeSession",
        *,
        reason: str,
    ) -> None:
        close_task = asyncio.create_task(
            self._close_http_bridge_session(session),
            name=f"http-bridge-close-{_hash_identifier(session.key.affinity_key)}",
        )

        def _track_close_task_after_interruption(*, interruption: str) -> None:
            if close_task.done():
                return
            self._background_cleanup_tasks.add(close_task)

            def _close_done(done_task: asyncio.Task[None]) -> None:
                self._background_cleanup_tasks.discard(done_task)
                try:
                    done_task.result()
                except asyncio.CancelledError:
                    logger.warning(
                        "http_bridge_session_close_cancelled_after_%s reason=%s bridge_kind=%s "
                        "bridge_key=%s account_id=%s model=%s",
                        interruption,
                        reason,
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        session.account.id,
                        session.request_model,
                    )
                except Exception:
                    logger.warning(
                        "http_bridge_session_close_failed_after_%s reason=%s bridge_kind=%s "
                        "bridge_key=%s account_id=%s model=%s",
                        interruption,
                        reason,
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        session.account.id,
                        session.request_model,
                        exc_info=True,
                    )

            close_task.add_done_callback(_close_done)

        try:
            await asyncio.wait_for(
                asyncio.shield(close_task),
                timeout=HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _track_close_task_after_interruption(interruption="timeout")
            logger.warning(
                "http_bridge_session_close_timeout reason=%s bridge_kind=%s bridge_key=%s "
                "account_id=%s model=%s timeout_seconds=%.1f background_cleanup_tasks=%d",
                reason,
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                session.account.id,
                session.request_model,
                HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
                len(self._background_cleanup_tasks),
            )
        except asyncio.CancelledError:
            _track_close_task_after_interruption(interruption="cancellation")
            raise
        except Exception:
            logger.warning(
                "http_bridge_session_close_failed reason=%s bridge_kind=%s bridge_key=%s account_id=%s model=%s",
                reason,
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                session.account.id,
                session.request_model,
                exc_info=True,
            )

    def _schedule_http_bridge_session_closes(
        self,
        sessions: list["_HTTPBridgeSession"],
        *,
        reason: str,
    ) -> None:
        for session in sessions:
            if len(self._background_cleanup_tasks) >= HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD:
                logger.warning(
                    "http_bridge_background_cleanup_backlog action=session_close count=%d threshold=%d reason=%s",
                    len(self._background_cleanup_tasks),
                    HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD,
                    reason,
                )
            self._schedule_cancel_safe_cleanup(
                self._close_http_bridge_session_bounded(session, reason=reason),
                action="http_bridge_session_close",
                request_id=_hash_identifier(session.key.affinity_key),
            )

    async def _drain_http_bridge_background_cleanup_tasks(self, *, reason: str) -> None:
        tasks = [
            task
            for task in self._background_cleanup_tasks
            if not task.done()
            and (
                task.get_name().startswith("proxy-http_bridge_session_close-")
                or task.get_name().startswith("http-bridge-close-")
            )
        ]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True),
                timeout=HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "http_bridge_background_cleanup_drain_timeout reason=%s count=%d timeout_seconds=%.1f",
                reason,
                len(tasks),
                HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )

    async def _fail_http_bridge_inflight_session_creation(
        self,
        key: "_HTTPBridgeSessionKey",
        inflight_future: asyncio.Future["_HTTPBridgeSession"] | None,
        exc: BaseException,
    ) -> bool:
        if inflight_future is None:
            return False
        async with self._http_bridge_lock:
            current_future = self._http_bridge_inflight_sessions.get(key)
            if current_future is not inflight_future:
                return False
            self._http_bridge_inflight_sessions.pop(key, None)
            if inflight_future.done():
                return True
            if isinstance(exc, asyncio.CancelledError):
                inflight_future.cancel()
            else:
                inflight_future.set_exception(exc)
                inflight_future.exception()
            return True

    async def _evict_http_bridge_inflight_waiter(
        self,
        inflight_future: asyncio.Future["_HTTPBridgeSession"],
        exc: BaseException,
    ) -> "_HTTPBridgeSessionKey | None":
        async with self._http_bridge_lock:
            stale_key = None
            for candidate_key, candidate_future in self._http_bridge_inflight_sessions.items():
                if candidate_future is inflight_future:
                    stale_key = candidate_key
                    break
            if stale_key is None:
                return None
            self._http_bridge_inflight_sessions.pop(stale_key, None)
            if not inflight_future.done():
                inflight_future.set_exception(exc)
                inflight_future.exception()
            return stale_key

    async def _http_bridge_account_still_servable(self, account) -> bool:

        if account is None:
            return False
        if getattr(account, "id", None) is None:
            return getattr(account, "status", None) == AccountStatus.ACTIVE
        from app.core.providers import OPENAI_PROVIDER_NAME, normalize_provider_name
        from app.modules.proxy.load_balancer import (
            RuntimeState,
            _select_long_window_entry,
            _state_from_account,
        )

        async with self._repo_factory() as repos:
            fresh = await repos.accounts.get_by_id(account.id)
            if fresh is None or fresh.status != AccountStatus.ACTIVE:
                return False
            if normalize_provider_name(fresh.provider) != OPENAI_PROVIDER_NAME:
                return True
            primary = await repos.usage.latest_entry_for_account(fresh.id, window="primary")
            secondary = await repos.usage.latest_entry_for_account(fresh.id, window="secondary")
            monthly = await repos.usage.latest_entry_for_account(fresh.id, window="monthly")
            long_window = _select_long_window_entry(
                account=fresh,
                monthly_entry=monthly,
                secondary_entry=secondary,
            )
            state = _state_from_account(
                account=fresh,
                primary_entry=primary,
                secondary_entry=long_window,
                runtime=RuntimeState(),
            )
            return state.status == AccountStatus.ACTIVE

    async def close_all_http_bridge_sessions(self) -> None:
        async with self._http_bridge_lock:
            sessions_to_close = list(self._http_bridge_sessions.values())
            inflight_futures = list(self._http_bridge_inflight_sessions.values())
            self._http_bridge_sessions.clear()
            self._http_bridge_inflight_sessions.clear()
            self._http_bridge_previous_response_index.clear()

        shutdown_error = ProxyResponseError(
            503,
            openai_error(
                "upstream_unavailable",
                "HTTP responses session bridge is shutting down",
                error_type="server_error",
            ),
        )
        for inflight_future in inflight_futures:
            if inflight_future.done():
                continue
            inflight_future.set_exception(shutdown_error)
            inflight_future.exception()

        for session in sessions_to_close:
            await self._close_http_bridge_session(session)
        await self._drain_http_bridge_background_cleanup_tasks(reason="shutdown")

    async def mark_http_bridge_draining(self) -> None:
        try:
            await self._durable_bridge.mark_instance_draining(
                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            )
        except Exception:
            logger.warning("Failed to mark durable HTTP bridge sessions draining", exc_info=True)

    def _prune_http_bridge_sessions_locked(self) -> list["_HTTPBridgeSession"]:
        now = _service_time().monotonic()

        if now - self._http_bridge_last_idle_prune_monotonic < HTTP_BRIDGE_IDLE_PRUNE_MIN_INTERVAL_SECONDS:
            return []
        self._http_bridge_last_idle_prune_monotonic = now
        stale_keys: list[_HTTPBridgeSessionKey] = []
        for key, session in self._http_bridge_sessions.items():
            if session.closed:
                stale_keys.append(key)
                continue
            pending_count = self._http_bridge_pending_count_nowait(
                session,
                context="idle_prune",
            )
            if pending_count is None:
                continue
            if pending_count:
                continue
            if now - session.last_used_at < session.idle_ttl_seconds:
                continue
            stale_keys.append(key)
        sessions_to_close: list[_HTTPBridgeSession] = []
        for key in stale_keys:
            session = self._detach_http_bridge_session_locked(key)
            if session is not None:
                _log_http_bridge_event(
                    "evict_idle",
                    key,
                    account_id=session.account.id,
                    model=session.request_model,
                    cache_key_family=key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                sessions_to_close.append(session)
        return sessions_to_close

    async def _close_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        turn_state_lock_held: bool = False,
    ) -> None:
        session.closed = True
        if turn_state_lock_held:
            self._unregister_http_bridge_turn_states_locked(session)
            self._unregister_http_bridge_previous_response_ids_locked(session)
        else:
            await self._unregister_http_bridge_turn_states(session)
            await self._unregister_http_bridge_previous_response_ids(session)
        account_lease = getattr(session, "account_lease", None)
        try:
            await self._load_balancer.release_account_lease(account_lease)
        except Exception:
            logger.warning("Failed to release HTTP bridge account lease during close", exc_info=True)
        finally:
            session.account_lease = None
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.release_live_session(
                    session_id=session.durable_session_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    draining=shutdown_state.is_bridge_drain_active(),
                )
            except Exception:
                logger.warning("Failed to release durable HTTP bridge session", exc_info=True)
        upstream_reader = session.upstream_reader
        if upstream_reader is not None:
            if upstream_reader is asyncio.current_task():
                session.upstream_reader = None
            else:
                await _await_cancelled_task(upstream_reader, label="http bridge upstream reader")
                if session.upstream_reader is upstream_reader:
                    session.upstream_reader = None
        try:
            await session.upstream.close()
        except Exception:
            logger.debug("Failed to close HTTP bridge upstream websocket", exc_info=True)
        pending_requests = getattr(session, "pending_requests", None)
        pending_lock = getattr(session, "pending_lock", None)
        response_create_gate = getattr(session, "response_create_gate", None)
        if pending_requests is not None and pending_lock is not None:
            async with pending_lock:
                session.queued_request_count = 0
            await self._fail_pending_websocket_requests(
                account=session.account,
                account_id_value=session.account.id,
                pending_requests=pending_requests,
                pending_lock=pending_lock,
                error_code="stream_incomplete",
                error_message="HTTP bridge session closed before response.completed",
                api_key=None,
                response_create_gate=response_create_gate,
            )
        _log_http_bridge_event(
            "close",
            session.key,
            account_id=session.account.id,
            model=session.request_model,
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )

    def _promote_http_bridge_session_to_codex_affinity(
        self,
        session: "_HTTPBridgeSession",
        *,
        turn_state: str,
        settings: Settings,
    ) -> None:
        session.affinity = _AffinityPolicy(key=turn_state, kind=StickySessionKind.CODEX_SESSION)
        session.codex_session = True
        session.downstream_turn_state = turn_state
        session.downstream_turn_state_aliases.add(turn_state)
        session.idle_ttl_seconds = max(
            session.idle_ttl_seconds,
            float(settings.http_responses_session_bridge_codex_idle_ttl_seconds),
        )
        session.headers = _headers_with_turn_state(session.headers, turn_state)


class BridgeDurableSessionMixin(BridgeOperationsProtocol):
    async def _claim_durable_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        allow_takeover: bool,
        force_owner_epoch_advance: bool = False,
    ) -> None:
        current_instance = _service_get_settings().http_responses_session_bridge_instance_id
        try:
            lookup: DurableBridgeLookup | None = None
            for claim_attempt in range(2):
                lookup = await self._durable_bridge.claim_live_session(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    instance_id=current_instance,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                    account_id=session.account.id,
                    model=session.request_model,
                    service_tier=None,
                    latest_turn_state=session.downstream_turn_state,
                    latest_response_id=None,
                    allow_takeover=allow_takeover,
                    force_owner_epoch_advance=force_owner_epoch_advance or claim_attempt > 0,
                )
                if lookup.owner_instance_id == current_instance:
                    break
                if not allow_takeover or claim_attempt > 0:
                    break
                await asyncio.sleep(0)
            assert lookup is not None
            if lookup.owner_instance_id != current_instance:
                _log_http_bridge_event(
                    "owner_mismatch_retry",
                    session.key,
                    account_id=None,
                    model=session.request_model,
                    detail=(
                        "expected_instance="
                        f"{lookup.owner_instance_id}, current_instance={current_instance}, outcome=claim_rejected"
                    ),
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    owner_check_applied=True,
                )
                if PROMETHEUS_AVAILABLE and bridge_instance_mismatch_total is not None:
                    bridge_instance_mismatch_total.labels(outcome="retry").inc()
                raise ProxyResponseError(
                    409,
                    openai_error(
                        "bridge_instance_mismatch",
                        "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
                        error_type="server_error",
                    ),
                )
            session.durable_session_id = lookup.session_id
            session.durable_owner_epoch = lookup.owner_epoch
            session.headers = _headers_with_turn_state(session.headers, session.downstream_turn_state)
            if (
                PROMETHEUS_AVAILABLE
                and bridge_durable_recover_total is not None
                and allow_takeover
                and lookup.owner_epoch > 1
            ):
                bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _record_bridge_reattach(path="restart_takeover", outcome="success")
            if session.key.affinity_kind == "session_header":
                await self._durable_bridge.register_session_header(
                    session_id=lookup.session_id,
                    api_key_id=session.key.api_key_id,
                    session_header=session.key.affinity_key,
                )
        except Exception as exc:
            if _is_missing_durable_bridge_table_error(exc):
                logger.warning("Durable bridge tables missing; using in-memory bridge session fallback", exc_info=True)
                return
            raise

    async def _refresh_durable_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
    ) -> None:
        if session.durable_session_id is None or session.durable_owner_epoch is None:
            return
        try:
            lookup = await self._durable_bridge.renew_live_session(
                session_id=session.durable_session_id,
                api_key_id=session.key.api_key_id,
                instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                owner_epoch=session.durable_owner_epoch,
                lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                latest_turn_state=session.downstream_turn_state,
                latest_response_id=None,
            )
            if lookup is not None:
                session.durable_owner_epoch = lookup.owner_epoch
        except Exception:
            logger.warning("Failed to renew durable HTTP bridge session lease", exc_info=True)


class BridgeSessionConnectMixin(BridgeOperationsProtocol):
    async def _create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        response_create_concurrency: int = 64,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        require_preferred_account: bool = False,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession":
        request_state = _WebSocketRequestState(
            request_id=f"http_bridge_connect_{uuid4().hex}",
            model=request_model,
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=_service_time().monotonic(),
            transport=REQUEST_TRANSPORT_HTTP,
        )
        deadline = _websocket_connect_deadline(request_state, _service_get_settings().proxy_request_budget_seconds)
        settings = await _service_get_settings_cache().get()
        excluded_account_ids: set[str] = set()
        cooldown = self._http_bridge_account_cooldowns.get(key)
        if cooldown is not None:
            if _service_time().monotonic() >= cooldown.expires_at:
                self._http_bridge_account_cooldowns.pop(key, None)
            elif cooldown.account_id != preferred_account_id:
                excluded_account_ids.add(cooldown.account_id)
        retry_same_account_once = preferred_account_id is not None
        preferred_candidate_id = preferred_account_id
        selected_account_lease: AccountLease | None = None

        account_rejection: ProxyResponseError | None = None
        while True:
            select_kwargs = {
                "request_id": request_state.request_log_id or request_state.request_id,
                "kind": "http_bridge",
                "request_stage": request_stage,
                "api_key": api_key,
                "sticky_key": affinity.key,
                "sticky_kind": affinity.kind,
                "reallocate_sticky": affinity.reallocate_sticky,
                "sticky_max_age_seconds": affinity.max_age_seconds,
                "prefer_earlier_reset_accounts": settings.prefer_earlier_reset_accounts,
                "prefer_earlier_reset_window": _prefer_earlier_reset_window(settings),
                "routing_strategy": _routing_strategy(settings),
                "model": request_model,
                "exclude_account_ids": excluded_account_ids,
                "preferred_account_id": preferred_candidate_id,
                "lease_kind": "stream",
                "estimated_lease_tokens": _estimated_lease_tokens_from_request_usage_budget(request_usage_budget),
                "fallback_on_preferred_account_unavailable": fallback_on_preferred_account_unavailable,
            }
            selection = await self._select_account_with_budget_for_stream(deadline, **select_kwargs)
            selected_account_lease = selection.lease
            account = selection.account
            if account is not None and account_rejection is not None and account.id in excluded_account_ids:
                account = None
            if account is None:
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=None,
                )
                if account_rejection is not None:
                    raise account_rejection
                status_code = 429 if _is_local_account_cap_code(selection.error_code) else 503
                error_type = "rate_limit_error" if status_code == 429 else "server_error"
                raise ProxyResponseError(
                    status_code,
                    enrich_selection_error(
                        openai_error(
                            selection.error_code or "no_accounts",
                            selection.error_message or "No active accounts available",
                            error_type=error_type,
                        ),
                        selection,
                        requested_model=request_model,
                    ),
                )
            if require_preferred_account and preferred_account_id is not None and account.id != preferred_account_id:
                message = "Previous response owner account is unavailable; retry later."
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "previous_response_owner_unavailable",
                        message,
                        error_type="server_error",
                    ),
                )
            selected_is_preferred = preferred_account_id is not None and account.id == preferred_account_id
            try:
                account = await self._ensure_fresh_with_budget(
                    account,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                connect_headers = _headers_with_turn_state(headers, _sticky_key_from_turn_state_header(headers))
                upstream = await _call_with_supported_optional_kwargs(
                    self._open_upstream_websocket_with_budget,
                    account,
                    connect_headers,
                    optional_kwargs={"request_state": request_state},
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                _record_same_account_takeover(
                    preferred_account_id=preferred_account_id,
                    selected_account_id=account.id,
                )
                break
            except ProxyResponseError as exc:
                if (
                    exc.status_code == 403
                    and _remaining_budget_seconds(deadline) > 0
                    and not (require_preferred_account and selected_is_preferred)
                ):
                    await self._handle_proxy_error(account, exc)
                    account_rejection = exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if exc.status_code != 401 or _remaining_budget_seconds(deadline) <= 0:
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    raise
                try:
                    account = await self._ensure_fresh_with_budget(
                        account,
                        force=True,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                    )
                    connect_headers = _headers_with_turn_state(headers, _sticky_key_from_turn_state_header(headers))
                    upstream = await self._open_upstream_websocket_with_budget(
                        account,
                        connect_headers,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                        request_state=request_state,
                    )
                    _record_same_account_takeover(
                        preferred_account_id=preferred_account_id,
                        selected_account_id=account.id,
                    )
                    break
                except ProxyResponseError as retry_exc:
                    if retry_exc.status_code != 401:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    await self._handle_proxy_error(account, retry_exc)
                    if require_preferred_account and selected_is_preferred:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    if require_preferred_account and selected_is_preferred:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        continue
                    if require_preferred_account:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "no_accounts",
                                "Preferred account is unavailable; retry later.",
                                error_type="server_error",
                            ),
                        ) from exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if exc.is_permanent:
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    rejection = ProxyResponseError(
                        401,
                        openai_error(
                            "invalid_api_key",
                            exc.message,
                            error_type="authentication_error",
                        ),
                    )
                    if _remaining_budget_seconds(deadline) <= 0:
                        raise rejection from exc
                    account_rejection = rejection
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    continue
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _raise_proxy_unavailable(exc.message or "Temporary upstream refresh failure")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        continue
                    if require_preferred_account:
                        await self._load_balancer.release_account_lease(selected_account_lease)
                        selected_account_lease = None
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "no_accounts",
                                "Preferred account is unavailable; retry later.",
                                error_type="server_error",
                            ),
                        ) from exc
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await self._load_balancer.release_account_lease(selected_account_lease)
                    selected_account_lease = None
                    continue
                if request_stage == "first_turn":
                    _record_bridge_first_turn_timeout()
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                _raise_proxy_unavailable(str(exc) or "Request to upstream timed out")
            except BaseException:
                await self._load_balancer.release_account_lease(selected_account_lease)
                selected_account_lease = None
                raise
        session = _HTTPBridgeSession(
            key=key,
            headers=connect_headers,
            affinity=affinity,
            api_key=api_key,
            request_model=request_model,
            account=account,
            upstream=upstream,
            upstream_control=_WebSocketUpstreamControl(),
            pending_requests=deque(),
            pending_lock=anyio.Lock(),
            response_create_gate=asyncio.Semaphore(response_create_concurrency),
            queued_request_count=0,
            lifecycle_lock=anyio.Lock(),
            last_used_at=_service_time().monotonic(),
            idle_ttl_seconds=idle_ttl_seconds,
            codex_session=affinity.kind == StickySessionKind.CODEX_SESSION,
            prewarm_lock=anyio.Lock(),
            upstream_turn_state=_upstream_turn_state_from_socket(upstream),
            downstream_turn_state=None,
            account_lease=selected_account_lease,
        )
        _copy_websocket_route_metadata_to_session(session, request_state)
        session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        return session


class BridgeSessionReconnectMixin(BridgeOperationsProtocol):
    async def _reconnect_http_bridge_session(
        self,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        restart_reader: bool = False,
        require_security_work_authorized: bool = False,
    ) -> None:
        old_account_id = session.account.id
        old_upstream = session.upstream
        old_reader = session.upstream_reader if restart_reader else None
        if old_reader is not None:
            if old_reader is not asyncio.current_task():
                cancelled = await _await_cancelled_task(old_reader, label="http bridge upstream reader")
                if not cancelled:
                    session.closed = True
                    raise ProxyResponseError(
                        502,
                        openai_error(
                            "upstream_unavailable",
                            "HTTP responses session bridge reader did not shut down cleanly",
                        ),
                    )
        deadline = _websocket_connect_deadline(request_state, _service_get_settings().proxy_request_budget_seconds)
        settings = await _service_get_settings_cache().get()
        session.api_key = request_state.api_key
        skip_same_account = session.last_upstream_close_code in UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY
        forced_refresh_account_id = request_state.force_refresh_account_id
        excluded_account_ids: set[str] = set(request_state.excluded_account_ids)
        if skip_same_account:
            excluded_account_ids.add(session.account.id)
        retry_same_account_once = not skip_same_account and session.account.id not in excluded_account_ids
        if skip_same_account:
            preferred_candidate_id: str | None = None
        elif forced_refresh_account_id is not None:
            preferred_candidate_id = forced_refresh_account_id
        elif request_state.preferred_account_id is not None:
            preferred_candidate_id = request_state.preferred_account_id
        elif session.account.id not in excluded_account_ids:
            preferred_candidate_id = session.account.id
        else:
            preferred_candidate_id = None
        selected_account_lease: AccountLease | None = None

        async def release_selected_account_lease() -> None:
            nonlocal selected_account_lease
            lease = selected_account_lease
            selected_account_lease = None
            if lease is None:
                return
            if lease is session.account_lease:
                session.account_lease = None
            await self._load_balancer.release_account_lease(lease)

        while True:
            reuse_current_account_lease = (
                preferred_candidate_id == session.account.id and session.account_lease is not None
            )
            selection = await self._select_account_with_budget_for_stream(
                deadline,
                request_id=request_state.request_log_id or request_state.request_id,
                kind="http_bridge",
                request_stage="reattach",
                api_key=session.api_key,
                sticky_key=session.affinity.key,
                sticky_kind=session.affinity.kind,
                reallocate_sticky=session.affinity.reallocate_sticky,
                sticky_max_age_seconds=session.affinity.max_age_seconds,
                prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
                prefer_earlier_reset_window=_prefer_earlier_reset_window(settings),
                routing_strategy=_routing_strategy(settings),
                model=session.request_model,
                exclude_account_ids=excluded_account_ids,
                preferred_account_id=preferred_candidate_id,
                require_security_work_authorized=require_security_work_authorized,
                lease_kind=None if reuse_current_account_lease else "stream",
                estimated_lease_tokens=_estimated_lease_tokens_from_request_usage_budget(
                    request_state.request_usage_budget
                ),
                fallback_on_preferred_account_unavailable=not reuse_current_account_lease,
            )
            account = selection.account
            if account is None:
                await release_selected_account_lease()
                if reuse_current_account_lease and _remaining_budget_seconds(deadline) > 0:
                    preferred_candidate_id = None
                    continue
                _record_same_account_takeover(
                    preferred_account_id=session.account.id,
                    selected_account_id=None,
                )
                status_code = 429 if _is_local_account_cap_code(selection.error_code) else 503
                raise ProxyResponseError(
                    status_code,
                    enrich_selection_error(
                        openai_error(
                            selection.error_code or "no_accounts",
                            selection.error_message or "No active accounts available",
                            error_type="rate_limit_error" if status_code == 429 else "server_error",
                        ),
                        selection,
                    ),
                )
            selected_account_lease = (
                session.account_lease
                if reuse_current_account_lease and account.id == session.account.id
                else selection.lease
            )
            selected_is_preferred = account.id == session.account.id
            force_refresh = forced_refresh_account_id == account.id
            if forced_refresh_account_id is not None and account.id != forced_refresh_account_id:
                request_state.force_refresh_account_id = None
                if request_state.preferred_account_id == forced_refresh_account_id:
                    request_state.preferred_account_id = None
            try:
                account = await self._ensure_fresh_with_budget(
                    account,
                    force=force_refresh,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                )
                if force_refresh and request_state.force_refresh_account_id == account.id:
                    request_state.force_refresh_account_id = None
                connect_headers = _headers_with_turn_state(
                    session.headers,
                    _preferred_http_bridge_reconnect_turn_state(session),
                )
                upstream = await self._open_upstream_websocket_with_budget(
                    account,
                    connect_headers,
                    timeout_seconds=_remaining_budget_seconds(deadline),
                    request_state=request_state,
                )
                _copy_websocket_route_metadata_to_session(session, request_state)
                _record_same_account_takeover(
                    preferred_account_id=session.account.id,
                    selected_account_id=account.id,
                )
                break
            except ProxyResponseError as exc:
                if exc.status_code != 401 or _remaining_budget_seconds(deadline) <= 0:
                    await release_selected_account_lease()
                    raise
                try:
                    account = await self._ensure_fresh_with_budget(
                        account,
                        force=True,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                    )
                    connect_headers = _headers_with_turn_state(
                        session.headers,
                        _preferred_http_bridge_reconnect_turn_state(session),
                    )
                    upstream = await self._open_upstream_websocket_with_budget(
                        account,
                        connect_headers,
                        timeout_seconds=_remaining_budget_seconds(deadline),
                        request_state=request_state,
                    )
                    _copy_websocket_route_metadata_to_session(session, request_state)
                    _record_same_account_takeover(
                        preferred_account_id=session.account.id,
                        selected_account_id=account.id,
                    )
                    break
                except ProxyResponseError as retry_exc:
                    if retry_exc.status_code != 401:
                        await release_selected_account_lease()
                        raise
                    await self._handle_proxy_error(account, retry_exc)
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
                except RefreshError as refresh_exc:
                    if refresh_exc.is_permanent:
                        await self._load_balancer.mark_permanent_failure(account, refresh_exc.code)
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
            except RefreshError as exc:
                if exc.is_permanent:
                    await self._load_balancer.mark_permanent_failure(account, exc.code)
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once and not exc.is_permanent:
                        retry_same_account_once = False
                        await release_selected_account_lease()
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
                await release_selected_account_lease()
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if selected_is_preferred and _remaining_budget_seconds(deadline) > 0:
                    if retry_same_account_once:
                        retry_same_account_once = False
                        await release_selected_account_lease()
                        continue
                    excluded_account_ids.add(account.id)
                    preferred_candidate_id = None
                    await release_selected_account_lease()
                    continue
                await release_selected_account_lease()
                raise
        try:
            await old_upstream.close()
        except Exception:
            logger.debug("Failed to close HTTP bridge upstream websocket before reconnect", exc_info=True)
        if selected_account_lease is not session.account_lease:
            await self._load_balancer.release_account_lease(session.account_lease)
        session.account_lease = selected_account_lease
        session.account = account
        session.headers = connect_headers
        session.upstream = upstream
        session.upstream_control = _WebSocketUpstreamControl()
        session.closed = False
        session.last_upstream_close_code = None
        session.upstream_turn_state = _upstream_turn_state_from_socket(upstream) or session.upstream_turn_state
        if restart_reader:
            session.upstream_reader = asyncio.create_task(self._relay_http_bridge_upstream_messages(session))
        _log_http_bridge_event(
            "reconnect",
            session.key,
            account_id=account.id,
            model=session.request_model,
            detail=(
                f"request_stage=reattach, previous_account={old_account_id}, "
                f"preferred_account_id={old_account_id}, selected_account_id={account.id}, "
                f"durable_session_id={session.durable_session_id}"
            ),
            cache_key_family=session.key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )


class BridgeOwnerRecoveryMixin(BridgeOperationsProtocol):
    async def resolve_bridge_owner_forward(self, state: BridgeSessionAcquisition) -> None:
        if state.forwarded_request:
            _log_http_bridge_event(
                "owner_mismatch_forward_loop",
                state.key,
                account_id=None,
                model=state.request_model,
                detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=forward_loop_prevented",
                cache_key_family=state.key.affinity_kind,
                model_class=_extract_model_class(state.request_model) if state.request_model else None,
                owner_check_applied=True,
            )
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_forward_loop_prevented",
                    "HTTP bridge request was forwarded back to a non-owner instance; refusing takeover to avoid a forward loop",
                    error_type="server_error",
                ),
            )
        elif self._ring_membership is None:
            if _http_bridge_has_durable_recovery_anchor(
                previous_response_id=state.previous_response_id, durable_lookup=state.durable_lookup
            ):
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="owner_missing").inc()
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_ring",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
            elif _http_bridge_can_single_instance_owner_takeover_without_anchor(
                key=state.key,
                owner_instance=state.owner_instance,
                current_instance=state.current_instance,
                ring=state.ring,
            ):
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=single_instance_takeover_no_anchor",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
            else:
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_ring",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
        else:
            assert state.owner_instance is not None
            state.owner_endpoint = await self._ring_membership.resolve_endpoint(state.owner_instance)
            if state.owner_endpoint is None:
                if _http_bridge_has_durable_recovery_anchor(
                    previous_response_id=state.previous_response_id, durable_lookup=state.durable_lookup
                ):
                    if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                        bridge_durable_recover_total.labels(path="owner_missing").inc()
                    _log_http_bridge_event(
                        "owner_endpoint_missing_local_recover",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=True,
                    )
                    state.force_durable_takeover = True
                else:
                    _log_http_bridge_event(
                        "owner_mismatch_local_recover",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_endpoint",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=True,
                    )
                    state.force_durable_takeover = True
            elif _http_bridge_endpoint_matches_current_instance(state.owner_endpoint, state.settings):
                if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                    bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _log_http_bridge_event(
                    "owner_mismatch_local_recover",
                    state.key,
                    account_id=None,
                    model=state.request_model,
                    detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_same_endpoint",
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.request_model) if state.request_model else None,
                    owner_check_applied=True,
                )
                state.force_durable_takeover = True
            else:
                state.owner_forward = _HTTPBridgeOwnerForward(
                    owner_instance=state.owner_instance, owner_endpoint=state.owner_endpoint, key=state.key
                )

    async def resolve_bridge_owner(self, state: BridgeSessionAcquisition) -> None:
        if state.owner_check_required or state.key.affinity_kind == "prompt_cache":
            state.owner_instance = _durable_bridge_lookup_active_owner(state.durable_lookup)
            state.hard_continuity_lookup = state.owner_check_required or state.previous_response_id is not None
            state.ring_lookup_failed = False
            if state.owner_instance is None:
                try:
                    state.owner_instance = await _http_bridge_owner_instance(
                        state.key, state.settings, self._ring_membership
                    )
                except Exception as exc:
                    state.ring_lookup_failed = True
                    if state.hard_continuity_lookup:
                        _record_continuity_fail_closed(
                            surface="http_bridge",
                            reason="owner_metadata_unavailable",
                            previous_response_id=state.previous_response_id,
                            session_id=state.incoming_turn_state or state.incoming_session_key,
                            upstream_error_code="owner_lookup_failed",
                        )
                        raise ProxyResponseError(502, _http_bridge_owner_lookup_unavailable_error_envelope()) from exc
                    if _http_bridge_can_local_recover_without_ring(
                        key=state.key,
                        headers=state.headers,
                        previous_response_id=state.previous_response_id,
                        durable_lookup=state.durable_lookup,
                    ):
                        logger.warning("Bridge owner lookup failed; allowing local recovery path", exc_info=True)
                        state.owner_instance = state.settings.http_responses_session_bridge_instance_id
                    else:
                        raise
            try:
                state.current_instance, state.ring = await _active_http_bridge_instance_ring(
                    state.settings, self._ring_membership
                )
            except Exception as exc:
                if state.hard_continuity_lookup:
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="owner_metadata_unavailable",
                        previous_response_id=state.previous_response_id,
                        session_id=state.incoming_turn_state or state.incoming_session_key,
                        upstream_error_code="ring_lookup_failed",
                    )
                    raise ProxyResponseError(502, _http_bridge_owner_lookup_unavailable_error_envelope()) from exc
                if state.ring_lookup_failed or _http_bridge_can_local_recover_without_ring(
                    key=state.key,
                    headers=state.headers,
                    previous_response_id=state.previous_response_id,
                    durable_lookup=state.durable_lookup,
                ):
                    logger.warning("Bridge ring lookup failed; falling back to local recovery ring", exc_info=True)
                    state.current_instance = state.settings.http_responses_session_bridge_instance_id
                    state.ring = (state.current_instance,)
                else:
                    raise
            state.owner_mismatch = state.owner_instance is not None and state.owner_instance != state.current_instance
            if state.owner_mismatch and (len(state.ring) > 1 or state.durable_lookup is not None):
                if PROMETHEUS_AVAILABLE and bridge_owner_mismatch_total is not None:
                    bridge_owner_mismatch_total.labels(strength=_http_bridge_key_strength(state.key)).inc()
                if (
                    state.owner_check_required
                    and (not (state.previous_response_id is not None and state.allow_previous_response_recovery_rebind))
                    and (not state.allow_bootstrap_owner_rebind)
                ):
                    _log_http_bridge_event(
                        "owner_mismatch",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=forward",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=True,
                    )
                    if state.allow_forward_to_owner:
                        await self.resolve_bridge_owner_forward(state)
                    elif _http_bridge_has_durable_recovery_anchor(
                        previous_response_id=state.previous_response_id, durable_lookup=state.durable_lookup
                    ):
                        if PROMETHEUS_AVAILABLE and bridge_durable_recover_total is not None:
                            bridge_durable_recover_total.labels(path="owner_missing").inc()
                        _log_http_bridge_event(
                            "owner_mismatch_local_recover",
                            state.key,
                            account_id=None,
                            model=state.request_model,
                            detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover",
                            cache_key_family=state.key.affinity_kind,
                            model_class=_extract_model_class(state.request_model) if state.request_model else None,
                            owner_check_applied=True,
                        )
                        state.force_durable_takeover = True
                    else:
                        _log_http_bridge_event(
                            "owner_mismatch_local_recover",
                            state.key,
                            account_id=None,
                            model=state.request_model,
                            detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_recover_no_forward",
                            cache_key_family=state.key.affinity_kind,
                            model_class=_extract_model_class(state.request_model) if state.request_model else None,
                            owner_check_applied=True,
                        )
                        state.force_durable_takeover = True
                else:
                    _log_http_bridge_event(
                        "prompt_cache_locality_miss",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_rebind",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=False,
                    )
                    if _http_bridge_can_single_instance_prompt_cache_takeover_without_anchor(
                        key=state.key,
                        owner_instance=state.owner_instance,
                        current_instance=state.current_instance,
                        ring=state.ring,
                    ):
                        state.force_durable_takeover = True
                    elif state.allow_previous_response_recovery_rebind or state.allow_bootstrap_owner_rebind:
                        state.force_durable_takeover = True
                    _log_http_bridge_event(
                        "soft_locality_rebind",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail=f"expected_instance={state.owner_instance}, current_instance={state.current_instance}, outcome=local_rebind",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=False,
                    )
                    if PROMETHEUS_AVAILABLE:
                        if bridge_prompt_cache_locality_miss_total is not None:
                            bridge_prompt_cache_locality_miss_total.inc()
                        if bridge_soft_local_rebind_total is not None:
                            bridge_soft_local_rebind_total.inc()
                        if bridge_local_rebind_total is not None:
                            bridge_local_rebind_total.labels(reason="prompt_cache_locality_miss").inc()


class BridgeAliasReuseMixin(BridgeOperationsProtocol):
    async def resolve_bridge_turn_alias(self, state: BridgeSessionAcquisition) -> bool:
        if (
            state.incoming_turn_state is not None
            and state.forwarded_affinity is None
            and (not state.preserve_durable_canonical_key)
        ):
            state.alias_index_key = _http_bridge_turn_state_alias_key(state.incoming_turn_state, state.api_key_id)
            state.alias_key = self._http_bridge_turn_state_index.get(state.alias_index_key)
            if state.alias_key is not None:
                state.key = state.alias_key
                state.alias_session = self._http_bridge_sessions.get(state.alias_key)
                if (
                    state.alias_session is None
                    or state.alias_session.closed
                    or (not await self._http_bridge_account_still_servable(state.alias_session.account))
                    or (
                        not _http_bridge_session_matches_preferred_account(
                            session=state.alias_session,
                            previous_response_id=state.previous_response_id,
                            preferred_account_id=state.preferred_account_id,
                            require_preferred_account=not state.fallback_on_preferred_account_unavailable,
                        )
                    )
                ):
                    self._http_bridge_turn_state_index.pop(state.alias_index_key, None)
                    state.key = _HTTPBridgeSessionKey("turn_state_header", state.incoming_turn_state, state.api_key_id)
                else:
                    self._promote_http_bridge_session_to_codex_affinity(
                        state.alias_session, turn_state=state.incoming_turn_state, settings=state.settings
                    )
                    for alias in state.alias_session.downstream_turn_state_aliases:
                        self._http_bridge_turn_state_index[
                            _http_bridge_turn_state_alias_key(alias, state.alias_session.key.api_key_id)
                        ] = state.alias_session.key
                    state.key = state.alias_session.key
            elif state.incoming_turn_state.startswith("http_turn_"):
                if state.previous_response_id is not None:
                    state.previous_alias_key = _http_bridge_previous_response_alias_key(
                        state.previous_response_id, state.api_key_id
                    )
                    state.previous_key = self._http_bridge_previous_response_index.get(state.previous_alias_key)
                    state.previous_session = None
                    if state.previous_key is not None:
                        state.previous_session = self._http_bridge_sessions.get(state.previous_key)
                    if (
                        state.previous_session is not None
                        and (not state.previous_session.closed)
                        and await self._http_bridge_account_still_servable(state.previous_session.account)
                        and _http_bridge_session_matches_preferred_account(
                            session=state.previous_session,
                            previous_response_id=state.previous_response_id,
                            preferred_account_id=state.preferred_account_id,
                            require_preferred_account=not state.fallback_on_preferred_account_unavailable,
                        )
                    ):
                        state.key = state.previous_session.key
                        self._promote_http_bridge_session_to_codex_affinity(
                            state.previous_session, turn_state=state.incoming_turn_state, settings=state.settings
                        )
                        state.previous_session.downstream_turn_state_aliases.add(state.incoming_turn_state)
                        for alias in state.previous_session.downstream_turn_state_aliases:
                            self._http_bridge_turn_state_index[
                                _http_bridge_turn_state_alias_key(alias, state.previous_session.key.api_key_id)
                            ] = state.previous_session.key
                        return True
                    if state.previous_key is not None:
                        self._http_bridge_previous_response_index.pop(state.previous_alias_key, None)
                if state.incoming_session_key is not None:
                    state.key = _HTTPBridgeSessionKey("session_header", state.incoming_session_key, state.api_key_id)
                    state.used_session_header_fallback = True
                else:
                    state.key = _HTTPBridgeSessionKey("turn_state_header", state.incoming_turn_state, state.api_key_id)
                    state.missing_turn_state_alias = True
        return False

    async def reuse_bridge_session(self, state: BridgeSessionAcquisition) -> _HTTPBridgeSession | None:
        state.pruned_sessions = self._prune_http_bridge_sessions_locked()
        if state.pruned_sessions:
            if any((session.key == state.key for session in state.pruned_sessions)):
                state.force_durable_takeover = True
            self._schedule_http_bridge_session_closes(state.pruned_sessions, reason="registry_detach")
        state.existing = self._http_bridge_sessions.get(state.key)
        if (
            state.existing is not None
            and (not state.existing.closed)
            and await self._http_bridge_account_still_servable(state.existing.account)
            and _http_bridge_session_allows_api_key(state.existing, state.api_key)
            and _http_bridge_session_reusable_for_request(
                session=state.existing,
                key=state.key,
                incoming_turn_state=state.incoming_turn_state,
                previous_response_id=state.previous_response_id,
            )
            and _http_bridge_session_matches_preferred_account(
                session=state.existing,
                previous_response_id=state.previous_response_id,
                preferred_account_id=state.preferred_account_id,
                require_preferred_account=not state.fallback_on_preferred_account_unavailable,
            )
        ):
            state.current_instance = state.settings.http_responses_session_bridge_instance_id
            if _durable_bridge_lookup_allows_local_reuse(state.durable_lookup, current_instance=state.current_instance):
                state.existing.api_key = state.api_key
                state.existing.request_model = state.request_model
                state.existing.last_used_at = _service_time().monotonic()
                await self._refresh_durable_http_bridge_session(state.existing)
                _log_http_bridge_event(
                    "reuse",
                    state.key,
                    account_id=state.existing.account.id,
                    model=state.existing.request_model,
                    pending_count=self._http_bridge_pending_count_nowait(state.existing, context="reuse_log"),
                    cache_key_family=state.key.affinity_kind,
                    model_class=_extract_model_class(state.existing.request_model)
                    if state.existing.request_model
                    else None,
                )
                return state.existing
            state.old_account_id = state.existing.account.id
            state.detached = self._detach_http_bridge_session_locked(state.key, expected_session=state.existing)
            if state.detached is not None:
                state.force_durable_takeover = True
                self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
            state.existing = None
        if (
            state.existing is not None
            and (not state.existing.closed)
            and await self._http_bridge_account_still_servable(state.existing.account)
        ):
            state.old_account_id = state.existing.account.id
            state.retiring_with_visible_requests = _http_bridge_session_retiring_with_visible_requests(state.existing)
            state.detached = self._detach_http_bridge_session_locked(
                state.key, expected_session=state.existing, mark_closed=not state.retiring_with_visible_requests
            )
            if state.detached is not None:
                state.force_durable_takeover = True
                if not state.retiring_with_visible_requests:
                    self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
            state.existing = None
        return None


class BridgeCapacityAdmissionMixin(BridgeOperationsProtocol):
    async def admit_bridge_session(self, state: BridgeSessionAcquisition) -> None:
        if state.owner_mismatch_error is None:
            state.inflight_future = self._http_bridge_inflight_sessions.get(state.key)
            if (
                state.previous_response_id is not None
                and state.inflight_future is None
                and (
                    state.existing is None
                    or state.existing.closed
                    or (not await self._http_bridge_account_still_servable(state.existing.account))
                )
            ):
                state.previous_alias_key = _http_bridge_previous_response_alias_key(
                    state.previous_response_id, state.api_key_id
                )
                state.previous_key = self._http_bridge_previous_response_index.get(state.previous_alias_key)
                if state.previous_key is not None:
                    state.previous_session = self._http_bridge_sessions.get(state.previous_key)
                    if (
                        state.previous_session is not None
                        and (not state.previous_session.closed)
                        and await self._http_bridge_account_still_servable(state.previous_session.account)
                    ):
                        state.key = state.previous_session.key
                        state.existing = state.previous_session
                        state.inflight_future = self._http_bridge_inflight_sessions.get(state.previous_key)
                        if state.incoming_turn_state:
                            self._promote_http_bridge_session_to_codex_affinity(
                                state.previous_session, turn_state=state.incoming_turn_state, settings=state.settings
                            )
                            state.previous_session.downstream_turn_state_aliases.add(state.incoming_turn_state)
                            for alias in state.previous_session.downstream_turn_state_aliases:
                                self._http_bridge_turn_state_index[
                                    _http_bridge_turn_state_alias_key(alias, state.previous_session.key.api_key_id)
                                ] = state.previous_session.key
                        if state.inflight_future is None:
                            state.previous_session.request_model = state.request_model
                            state.previous_session.last_used_at = _service_time().monotonic()
                            await self._refresh_durable_http_bridge_session(state.previous_session)
                            _log_http_bridge_event(
                                "reuse",
                                state.key,
                                account_id=state.previous_session.account.id,
                                model=state.previous_session.request_model,
                                pending_count=self._http_bridge_pending_count_nowait(
                                    state.previous_session, context="previous_response_reuse_log"
                                ),
                                cache_key_family=state.key.affinity_kind,
                                model_class=_extract_model_class(state.previous_session.request_model)
                                if state.previous_session.request_model
                                else None,
                            )
                            state.session_to_return_after_close = state.previous_session
                    else:
                        self._http_bridge_previous_response_index.pop(state.previous_alias_key, None)
            if (
                state.session_to_return_after_close is None
                and state.previous_response_id is not None
                and (not state.used_session_header_fallback)
                and (not state.allow_previous_response_recovery_rebind)
                and (state.durable_lookup is None)
            ):
                _record_continuity_fail_closed(
                    surface="http_bridge",
                    reason="continuity_lost",
                    previous_response_id=state.previous_response_id,
                    session_id=state.incoming_turn_state or state.incoming_session_key,
                )
                state.continuity_error = ProxyResponseError(502, _http_bridge_continuity_lost_error_envelope())
            elif state.missing_turn_state_alias and state.inflight_future is None and (state.durable_lookup is None):
                state.turn_state_scope_conflict = state.incoming_turn_state is not None and any(
                    (
                        alias == state.incoming_turn_state and alias_api_key != state.api_key_id
                        for alias, alias_api_key in self._http_bridge_turn_state_index
                    )
                )
                if state.turn_state_scope_conflict:
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="turn_state_scope_conflict",
                        previous_response_id=state.previous_response_id,
                        session_id=state.incoming_turn_state,
                    )
                    state.continuity_error = ProxyResponseError(
                        409,
                        openai_error(
                            "bridge_instance_mismatch",
                            "HTTP bridge turn-state is bound to a different API key scope",
                            error_type="server_error",
                        ),
                    )
                elif (
                    state.incoming_turn_state is not None
                    and state.incoming_turn_state.startswith("http_turn_")
                    and (not state.allow_forward_to_owner)
                ):
                    _record_continuity_fail_closed(
                        surface="http_bridge",
                        reason="generated_turn_state_continuity_lost",
                        previous_response_id=state.previous_response_id,
                        session_id=state.incoming_turn_state,
                    )
                    state.continuity_error = ProxyResponseError(
                        409,
                        openai_error(
                            "bridge_instance_mismatch",
                            "HTTP bridge continuity was lost for generated turn-state",
                            error_type="server_error",
                        ),
                    )
                else:
                    _log_http_bridge_event(
                        "turn_state_alias_miss_local_rebind",
                        state.key,
                        account_id=None,
                        model=state.request_model,
                        detail="outcome=local_rebind_without_alias",
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        owner_check_applied=state.owner_check_required,
                    )
            elif state.inflight_future is None:
                while (
                    len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= state.max_sessions
                    and self._http_bridge_sessions
                ):
                    state.evictable_sessions = []
                    for state.candidate_key, state.candidate_session in self._http_bridge_sessions.items():
                        state.pending_count = self._http_bridge_pending_count_nowait(
                            state.candidate_session, context="capacity_evict_scan"
                        )
                        if state.pending_count is None:
                            continue
                        if state.pending_count:
                            continue
                        state.evictable_sessions.append((state.candidate_key, state.candidate_session))
                    if not state.evictable_sessions:
                        break
                    state.lru_key, state.lru_session = min(
                        state.evictable_sessions, key=lambda item: _http_bridge_eviction_priority(item[1])
                    )
                    _log_http_bridge_event(
                        "evict_lru",
                        state.lru_key,
                        account_id=state.lru_session.account.id,
                        model=state.lru_session.request_model,
                        cache_key_family=state.lru_key.affinity_kind,
                        model_class=_extract_model_class(state.lru_session.request_model)
                        if state.lru_session.request_model
                        else None,
                    )
                    state.detached = self._detach_http_bridge_session_locked(
                        state.lru_key, expected_session=state.lru_session
                    )
                    if state.detached is not None:
                        state.sessions_to_close_before_create.append(state.detached)
                if len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions) >= state.max_sessions:
                    if self._http_bridge_inflight_sessions:
                        state.capacity_wait_future = next(iter(self._http_bridge_inflight_sessions.values()))
                    else:
                        _log_http_bridge_event(
                            "capacity_exhausted_active_sessions",
                            state.key,
                            account_id=None,
                            model=state.request_model,
                            pending_count=len(self._http_bridge_sessions) + len(self._http_bridge_inflight_sessions),
                            cache_key_family=state.key.affinity_kind,
                            model_class=_extract_model_class(state.request_model) if state.request_model else None,
                        )
                        raise ProxyResponseError(
                            429,
                            local_overload_error(
                                "HTTP responses session bridge has no idle capacity",
                                code="capacity_exhausted_active_sessions",
                            ),
                        )
                else:
                    state.inflight_future = asyncio.get_running_loop().create_future()
                    self._http_bridge_inflight_sessions[state.key] = state.inflight_future
                    state.owns_creation = True


class BridgeSessionRegistrationMixin(BridgeOperationsProtocol):
    async def create_and_register_bridge_session(self, state: BridgeSessionAcquisition) -> _HTTPBridgeSession:
        state.created_session = None
        state.session_registered = False
        state.require_preferred_account = (
            state.previous_response_id is not None
            and state.preferred_account_id is not None
            or (state.preferred_account_id is not None and (not state.fallback_on_preferred_account_unavailable))
        )
        try:
            state.create_session = self._create_http_bridge_session
            state.create_kwargs = {
                "headers": state.headers,
                "affinity": state.affinity,
                "api_key": state.api_key,
                "request_model": state.request_model,
                "idle_ttl_seconds": state.effective_idle_ttl_seconds,
                "response_create_concurrency": state.response_create_concurrency,
                "request_stage": state.request_stage,
                "preferred_account_id": state.preferred_account_id,
                "require_preferred_account": state.require_preferred_account,
                "fallback_on_preferred_account_unavailable": state.fallback_on_preferred_account_unavailable,
                "request_usage_budget": state.request_usage_budget,
            }
            try:
                state.create_signature = inspect.signature(state.create_session)
            except (TypeError, ValueError):
                state.create_signature = None
            state.create_accepts_var_keyword = state.create_signature is not None and any(
                (
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in state.create_signature.parameters.values()
                )
            )
            if state.create_signature is not None and (not state.create_accepts_var_keyword):
                state.create_kwargs = {
                    name: value
                    for name, value in state.create_kwargs.items()
                    if name in state.create_signature.parameters
                }
            state.created_session = await state.create_session(state.key, **state.create_kwargs)
            await self._claim_durable_http_bridge_session(
                state.created_session,
                allow_takeover=state.force_durable_takeover
                or _http_bridge_allow_durable_takeover(state.durable_lookup),
                force_owner_epoch_advance=state.force_durable_takeover,
            )
            async with self._http_bridge_lock:
                state.current_future = self._http_bridge_inflight_sessions.get(state.key)
                if state.current_future is state.inflight_future:
                    self._http_bridge_inflight_sessions.pop(state.key, None)
                    self._http_bridge_sessions[state.key] = state.created_session
                    state.session_registered = True
                    if state.inflight_future is not None and (not state.inflight_future.done()):
                        state.inflight_future.set_result(state.created_session)
            if not state.session_registered:
                raise _http_bridge_startup_wait_timeout_error(
                    "http_bridge_session_registration", code="capacity_exhausted_active_sessions"
                )
        except BaseException as exc:
            async with self._http_bridge_lock:
                state.current_future = self._http_bridge_inflight_sessions.get(state.key)
                if state.current_future is state.inflight_future:
                    self._http_bridge_inflight_sessions.pop(state.key, None)
                    if state.inflight_future is not None and (not state.inflight_future.done()):
                        if isinstance(exc, asyncio.CancelledError):
                            state.inflight_future.cancel()
                        else:
                            state.inflight_future.set_exception(exc)
                            state.inflight_future.exception()
            if state.created_session is not None and (not state.session_registered):
                await self._close_http_bridge_session(state.created_session)
            raise
        assert state.created_session is not None
        _log_http_bridge_event(
            "create",
            state.key,
            account_id=state.created_session.account.id,
            model=state.created_session.request_model,
            detail=f"request_stage={state.request_stage}, preferred_account_id={state.preferred_account_id}, selected_account_id={state.created_session.account.id}, durable_session_id={state.created_session.durable_session_id}",
            cache_key_family=state.key.affinity_kind,
            model_class=_extract_model_class(state.created_session.request_model)
            if state.created_session.request_model
            else None,
        )
        if state.old_account_id is not None and state.old_account_id != state.created_session.account.id:
            _log_http_bridge_event(
                "reallocation_orphan",
                state.key,
                account_id=state.created_session.account.id,
                model=state.created_session.request_model,
                detail=f"old_account={state.old_account_id}",
                cache_key_family=state.key.affinity_kind,
                model_class=_extract_model_class(state.created_session.request_model)
                if state.created_session.request_model
                else None,
            )
        return state.created_session


class _HTTPBridgeMixin(
    BridgeLifecycleMixin,
    BridgeDurableSessionMixin,
    BridgeSessionConnectMixin,
    BridgeSessionReconnectMixin,
    BridgeOwnerRecoveryMixin,
    BridgeAliasReuseMixin,
    BridgeCapacityAdmissionMixin,
    BridgeSessionRegistrationMixin,
    _HTTPBridgeSessionRegistryMixin,
    _HTTPBridgeStreamingMixin,
    _HTTPBridgeOwnerForwardingMixin,
    _HTTPBridgeRequestSubmitMixin,
    _HTTPBridgeUpstreamEventsMixin,
    _HTTPBridgeServiceProtocol,
):
    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        response_create_concurrency: int = 64,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[False] = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession": ...

    @overload
    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        response_create_concurrency: int = 64,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: Literal[True],
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward": ...

    async def _get_or_create_http_bridge_session(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        headers: dict[str, str],
        affinity: _AffinityPolicy,
        api_key: ApiKeyData | None,
        request_model: str | None,
        idle_ttl_seconds: float,
        max_sessions: int,
        response_create_concurrency: int = 64,
        previous_response_id: str | None = None,
        gateway_safe_mode: bool = False,
        allow_forward_to_owner: bool = False,
        forwarded_request: bool = False,
        forwarded_affinity_kind: str | None = None,
        forwarded_affinity_key: str | None = None,
        allow_previous_response_recovery_rebind: bool = False,
        allow_bootstrap_owner_rebind: bool = False,
        durable_lookup: DurableBridgeLookup | None = None,
        request_stage: str = "first_turn",
        preferred_account_id: str | None = None,
        fallback_on_preferred_account_unavailable: bool = True,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> "_HTTPBridgeSession | _HTTPBridgeOwnerForward":
        state = BridgeSessionAcquisition(
            key=key,
            headers=headers,
            affinity=affinity,
            api_key=api_key,
            request_model=request_model,
            idle_ttl_seconds=idle_ttl_seconds,
            max_sessions=max_sessions,
            response_create_concurrency=response_create_concurrency,
            previous_response_id=previous_response_id,
            gateway_safe_mode=gateway_safe_mode,
            allow_forward_to_owner=allow_forward_to_owner,
            forwarded_request=forwarded_request,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            allow_previous_response_recovery_rebind=allow_previous_response_recovery_rebind,
            allow_bootstrap_owner_rebind=allow_bootstrap_owner_rebind,
            durable_lookup=durable_lookup,
            request_stage=request_stage,
            preferred_account_id=preferred_account_id,
            fallback_on_preferred_account_unavailable=fallback_on_preferred_account_unavailable,
            request_usage_budget=request_usage_budget,
        )
        state.settings = _service_get_settings()
        state.api_key_id = state.api_key.id if state.api_key is not None else None
        state.incoming_turn_state = _sticky_key_from_turn_state_header(state.headers)
        state.incoming_session_key = _sticky_key_from_session_header(state.headers)
        if await _http_bridge_should_wait_for_registration(self, state.key, state.settings):
            state.skip_registration_gate = False
            async with self._http_bridge_lock:
                state.existing = self._http_bridge_sessions.get(state.key)
                if state.existing is not None:
                    state.skip_registration_gate = True
                elif state.incoming_turn_state is not None:
                    state.alias_index_key = _http_bridge_turn_state_alias_key(
                        state.incoming_turn_state, state.api_key_id
                    )
                    state.alias_key = self._http_bridge_turn_state_index.get(state.alias_index_key)
                    if state.alias_key is not None and state.alias_key in self._http_bridge_sessions:
                        state.skip_registration_gate = True
            if not state.skip_registration_gate:
                import app.core.startup as startup_module

                state.registered = await startup_module.wait_for_bridge_registration(
                    timeout_seconds=state.settings.upstream_connect_timeout_seconds
                )
                if not state.registered:
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_owner_unreachable",
                            "HTTP bridge registration is not ready",
                            error_type="server_error",
                        ),
                    )
        state.effective_idle_ttl_seconds = state.idle_ttl_seconds
        state.forwarded_affinity = (
            _forwarded_http_bridge_session_key(
                state.headers,
                state.api_key,
                forwarded_affinity_kind=state.forwarded_affinity_kind,
                forwarded_affinity_key=state.forwarded_affinity_key,
            )
            if state.forwarded_request
            else None
        )
        state.old_account_id = None
        state.force_durable_takeover_after_detach = False
        while True:
            state.inflight_future = None
            state.capacity_wait_future = None
            state.owns_creation = False
            state.continuity_error = None
            state.owner_mismatch_error = None
            state.owner_forward = None
            state.force_durable_takeover = state.force_durable_takeover_after_detach
            state.missing_turn_state_alias = False
            state.used_session_header_fallback = False
            state.sessions_to_close_before_create = []
            state.session_to_return_after_close = None
            state.preserve_durable_canonical_key = (
                state.incoming_turn_state is not None
                and state.forwarded_affinity is None
                and (state.durable_lookup is not None)
                and (state.key.affinity_kind == state.durable_lookup.canonical_kind)
                and (state.key.affinity_key == state.durable_lookup.canonical_key)
                and (state.key.affinity_kind != "turn_state_header")
            )
            async with self._http_bridge_lock:
                if await self.resolve_bridge_turn_alias(state):
                    continue
                reused_session = await self.reuse_bridge_session(state)
                if reused_session is not None:
                    return reused_session
                if shutdown_state.is_bridge_drain_active() and (
                    not _http_bridge_can_recover_during_drain(
                        key=state.key,
                        headers=state.headers,
                        previous_response_id=state.previous_response_id,
                        durable_lookup=state.durable_lookup,
                    )
                ):
                    raise ProxyResponseError(
                        503,
                        openai_error(
                            "bridge_drain_active",
                            "HTTP bridge is draining — new sessions not accepted during shutdown",
                            error_type="server_error",
                        ),
                    )
                if shutdown_state.is_bridge_drain_active():
                    _record_bridge_drain_recovery_allowed()
                state.owner_check_required = _http_bridge_owner_check_required(
                    state.key, gateway_safe_mode=state.gateway_safe_mode
                )
                await self.resolve_bridge_owner(state)
                if state.existing is not None:
                    state.old_account_id = state.existing.account.id
                    _log_http_bridge_event(
                        "discard_stale",
                        state.key,
                        account_id=state.existing.account.id,
                        model=state.existing.request_model,
                        cache_key_family=state.key.affinity_kind,
                        model_class=_extract_model_class(state.existing.request_model)
                        if state.existing.request_model
                        else None,
                    )
                    state.detached = self._detach_http_bridge_session_locked(state.key, expected_session=state.existing)
                    if state.detached is not None:
                        state.force_durable_takeover = True
                        self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
                await self.admit_bridge_session(state)
            try:
                for state.session_to_close in state.sessions_to_close_before_create:
                    await self._close_http_bridge_session_bounded(state.session_to_close, reason="registry_detach")
            except BaseException as exc:
                if state.owns_creation:
                    await self._fail_http_bridge_inflight_session_creation(state.key, state.inflight_future, exc)
                raise
            if state.session_to_return_after_close is not None:
                return state.session_to_return_after_close
            if state.owner_forward is not None:
                return state.owner_forward
            if state.owner_mismatch_error is not None:
                raise state.owner_mismatch_error
            if state.continuity_error is not None:
                raise state.continuity_error
            if state.capacity_wait_future is not None:
                state.wait_timeout_seconds = _proxy_admission_wait_timeout_seconds(state.settings)
                try:
                    await asyncio.wait_for(
                        asyncio.shield(state.capacity_wait_future), timeout=state.wait_timeout_seconds
                    )
                except asyncio.CancelledError:
                    if state.capacity_wait_future.cancelled():
                        continue
                    raise
                except TimeoutError as exc:
                    state.timeout_error = _http_bridge_startup_wait_timeout_error(
                        "http_bridge_capacity", code="capacity_exhausted_active_sessions"
                    )
                    state.stale_key = await self._evict_http_bridge_inflight_waiter(
                        state.capacity_wait_future, state.timeout_error
                    )
                    _log_http_bridge_startup_wait_timeout(
                        stage="capacity",
                        timeout_seconds=state.wait_timeout_seconds,
                        key=state.stale_key or state.key,
                        request_model=state.request_model,
                        pending_count=len(self._http_bridge_sessions),
                        inflight_count=len(self._http_bridge_inflight_sessions),
                    )
                    raise state.timeout_error from exc
                except ProxyResponseError:
                    raise
                except Exception:
                    pass
                continue
            if state.inflight_future is not None and (not state.owns_creation):
                state.wait_timeout_seconds = _proxy_admission_wait_timeout_seconds(state.settings)
                try:
                    session = await asyncio.wait_for(
                        asyncio.shield(state.inflight_future), timeout=state.wait_timeout_seconds
                    )
                except asyncio.CancelledError:
                    if state.inflight_future.cancelled():
                        continue
                    raise
                except TimeoutError as exc:
                    state.timeout_error = _http_bridge_startup_wait_timeout_error(
                        "http_bridge_inflight_session", code="capacity_exhausted_active_sessions"
                    )
                    await self._fail_http_bridge_inflight_session_creation(
                        state.key, state.inflight_future, state.timeout_error
                    )
                    _log_http_bridge_startup_wait_timeout(
                        stage="inflight_session",
                        timeout_seconds=state.wait_timeout_seconds,
                        key=state.key,
                        request_model=state.request_model,
                        pending_count=len(self._http_bridge_sessions),
                        inflight_count=len(self._http_bridge_inflight_sessions),
                    )
                    raise state.timeout_error from exc
                except Exception:
                    raise
                if session is None:
                    continue
                if (
                    not session.closed
                    and await self._http_bridge_account_still_servable(session.account)
                    and _http_bridge_session_allows_api_key(session, state.api_key)
                    and _http_bridge_session_reusable_for_request(
                        session=session,
                        key=state.key,
                        incoming_turn_state=state.incoming_turn_state,
                        previous_response_id=state.previous_response_id,
                    )
                    and _http_bridge_session_matches_preferred_account(
                        session=session,
                        previous_response_id=state.previous_response_id,
                        preferred_account_id=state.preferred_account_id,
                        require_preferred_account=not state.fallback_on_preferred_account_unavailable,
                    )
                ):
                    state.current_instance = state.settings.http_responses_session_bridge_instance_id
                    if _durable_bridge_lookup_allows_local_reuse(
                        state.durable_lookup, current_instance=state.current_instance
                    ):
                        session.api_key = state.api_key
                        session.request_model = state.request_model
                        session.last_used_at = _service_time().monotonic()
                        return session
                if not session.closed and await self._http_bridge_account_still_servable(session.account):
                    state.old_account_id = session.account.id
                    state.retiring_with_visible_requests = _http_bridge_session_retiring_with_visible_requests(session)
                    async with self._http_bridge_lock:
                        state.detached = self._detach_http_bridge_session_locked(
                            state.key, expected_session=session, mark_closed=not state.retiring_with_visible_requests
                        )
                    if state.detached is not None:
                        state.force_durable_takeover_after_detach = True
                    if state.detached is not None and (not state.retiring_with_visible_requests):
                        self._schedule_http_bridge_session_closes([state.detached], reason="registry_detach")
                continue
            return await self.create_and_register_bridge_session(state)
