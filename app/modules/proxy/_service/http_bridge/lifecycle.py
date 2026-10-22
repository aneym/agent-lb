from __future__ import annotations

import asyncio

import anyio

from app.core import shutdown as shutdown_state
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import Settings
from app.core.errors import openai_error
from app.db.models import AccountStatus, StickySessionKind
from app.modules.proxy._service.http_bridge.acquisition_state import (
    HTTP_BRIDGE_BACKGROUND_CLEANUP_WARN_THRESHOLD,
    HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
    HTTP_BRIDGE_IDLE_PRUNE_MIN_INTERVAL_SECONDS,
    logger,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_request_counts_against_queue,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.operation_contract import BridgeOperationsProtocol
from app.modules.proxy._service.http_bridge.service_stubs import (
    _await_cancelled_task,
    _headers_with_turn_state,
    _service_get_settings,
    _service_time,
)
from app.modules.proxy._service.observability import _hash_identifier as _hash_identifier
from app.modules.proxy._service.support import _HTTPBridgeSession, _HTTPBridgeSessionKey
from app.modules.proxy.affinity import _AffinityPolicy, _extract_model_class


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
