from __future__ import annotations

import asyncio

import aiohttp

from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.modules.proxy._service.http_bridge.acquisition_state import (
    UPSTREAM_CLOSE_CODES_SKIP_SAME_ACCOUNT_RETRY,
    logger,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _log_http_bridge_event,
    _preferred_http_bridge_reconnect_turn_state,
)
from app.modules.proxy._service.http_bridge.operation_contract import BridgeOperationsProtocol
from app.modules.proxy._service.http_bridge.service_stubs import (
    _await_cancelled_task,
    _estimated_lease_tokens_from_request_usage_budget,
    _headers_with_turn_state,
    _is_local_account_cap_code,
    _prefer_earlier_reset_window,
    _record_same_account_takeover,
    _remaining_budget_seconds,
    _routing_strategy,
    _service_get_settings,
    _service_get_settings_cache,
    _upstream_turn_state_from_socket,
    _websocket_connect_deadline,
)
from app.modules.proxy._service.support import (
    _copy_websocket_route_metadata_to_session,
    _HTTPBridgeSession,
    _WebSocketRequestState,
    _WebSocketUpstreamControl,
)
from app.modules.proxy.affinity import _extract_model_class
from app.modules.proxy.load_balancer import AccountLease
from app.modules.proxy.selection_diagnostics import enrich_selection_error


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
