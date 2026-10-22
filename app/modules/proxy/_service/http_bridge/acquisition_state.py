from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypeVar

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
