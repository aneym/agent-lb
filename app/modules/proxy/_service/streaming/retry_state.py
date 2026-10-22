from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from app.modules.proxy.load_balancer import AccountLease

PROTOCOL_ONLY_STREAM_EVENT_TYPES = frozenset({"response.created", "response.in_progress", "response.queued"})


PROTOCOL_ONLY_TERMINAL_ERROR_TYPES = frozenset({"response.failed", "error"})


def retry_facade() -> Any:
    return sys.modules["app.modules.proxy.service"]


@dataclass
class RetryState:
    stop: bool = False
    RetryableStreamError: Any = None
    StreamSettlement: Any = None
    TransientStreamError: Any = None
    WebSocketUpstreamControl: Any = None
    apply_error_metadata: Any = None
    normalize_error_code: Any = None
    owner_lookup_session_id_from_headers: Any = None
    parse_openai_error: Any = None
    record_continuity_fail_closed: Any = None
    stream_settlement_error_payload: Any = None
    upstream_error_from_openai: Any = None
    protocol_only_stream_error: Any = None
    protocol_only_stream_event_type: Any = None
    account: Any = None
    account_id_value: Any = None
    account_leases: Any = None
    action: Any = None
    affinity: Any = None
    allow_retry_flag: Any = None
    any_attempt_logged: Any = None
    api_key: Any = None
    api_key_reservation: Any = None
    attempt: Any = None
    base_settings: Any = None
    buffered_line: Any = None
    candidates_remaining: Any = None
    classified: Any = None
    code: Any = None
    codex_session_affinity: Any = None
    current_lease: Any = None
    current_task: Any = None
    deadline: Any = None
    delay: Any = None
    downstream_visible: Any = None
    effective_attempt_timeout: Any = None
    error: Any = None
    error_code: Any = None
    error_message: Any = None
    error_param: Any = None
    error_payload: Any = None
    error_type: Any = None
    estimated_lease_tokens: Any = None
    event: Any = None
    event_type: Any = None
    excluded_account_ids: Any = None
    failed_response_id: Any = None
    file_preferred_account_id: Any = None
    file_required_preferred_account: Any = None
    had_prompt_cache_key: Any = None
    headers: Any = None
    last_retryable_stream_error: Any = None
    last_security_work_retry_error: Any = None
    last_transient_exc: Any = None
    line: Any = None
    max_attempts: Any = None
    message: Any = None
    no_accounts_msg: Any = None
    openai_cache_affinity: Any = None
    payload: Any = None
    prefer_earlier_reset: Any = None
    preferred_account_id: Any = None
    previous_response_lookup_session_id: Any = None
    propagate_http_errors: Any = None
    protocol_only_buffer: Any = None
    protocol_only_buffer_committed: Any = None
    protocol_only_terminal_error_code: Any = None
    protocol_only_terminal_error_message: Any = None
    proxy: Any = None
    release_coro: Any = None
    remaining_budget: Any = None
    request_id: Any = None
    request_identity: Any = None
    request_transport: Any = None
    require_preferred_account: Any = None
    require_security_work_authorized: Any = None
    resolved_file_account_id: Any = None
    retries_exhausted_msg: Any = None
    retry_protocol_only_failures: Any = None
    rewritten_file_account_id: Any = None
    routing_strategy: Any = None
    selection: Any = None
    selection_inputs: Any = None
    settings: Any = None
    settled: Any = None
    settlement: Any = None
    start: Any = None
    sticky_key_source: Any = None
    stream_timeout_tokens: Any = None
    suppress_text_done_events: Any = None
    tool_call_dedupe: Any = None
    transient_retries: Any = None
    upstream_stream_transport: Any = None
    upstream_stream_transport_override: Any = None
    useragent: Any = None
    useragent_group: Any = None


async def release_tracked_stream_lease(state: RetryState, lease: AccountLease | None) -> None:
    if lease is None:
        return
    try:
        state.account_leases.remove(lease)
    except ValueError:
        pass
    await state.proxy._load_balancer.release_account_lease(lease)
