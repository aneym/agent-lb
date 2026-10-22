from __future__ import annotations

from typing import Any

from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol


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
