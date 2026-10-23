from __future__ import annotations

import time
from typing import Collection, Iterable

from app.core.balancer.failures import _format_retry_hint
from app.core.balancer.opportunistic import _filter_opportunistic_candidates, _routing_policy
from app.core.balancer.quota_reset import plausible_rate_limit_reset_at as plausible_rate_limit_reset_at
from app.core.balancer.ranking import (
    _capacity_probe_sort_key_with_cost,
    _lowest_planner_cost_candidates,
    _planner_cost,
    _prefer_earlier_reset_candidates,
    _primary_usage_sort_key,
    _reset_drain_sort_key,
    _reset_preference_bucket,
    _select_capacity_weighted,
    _select_fill_first,
    _select_relative_availability,
    _sequential_drain_sort_key,
    _usage_exhausted,
    _usage_sort_key,
)
from app.core.balancer.selection_helpers import ERROR_BACKOFF_THRESHOLD as ERROR_BACKOFF_THRESHOLD
from app.core.balancer.selection_helpers import PROBE_QUIET_SECONDS as PROBE_QUIET_SECONDS
from app.core.balancer.selection_helpers import (
    RATE_LIMIT_RESET_MAX_HORIZON_SECONDS as RATE_LIMIT_RESET_MAX_HORIZON_SECONDS,
)
from app.core.balancer.selection_helpers import (
    RATE_LIMIT_RESET_ROUNDING_TOLERANCE_SECONDS as RATE_LIMIT_RESET_ROUNDING_TOLERANCE_SECONDS,
)
from app.core.balancer.selection_helpers import RATE_LIMITED_MIN_COOLDOWN_SECONDS as RATE_LIMITED_MIN_COOLDOWN_SECONDS
from app.core.balancer.selection_helpers import SECONDS_PER_DAY as SECONDS_PER_DAY
from app.core.balancer.selection_helpers import USAGE_LIMIT_REACHED as USAGE_LIMIT_REACHED
from app.core.balancer.selection_helpers import _configured_replica_salt as _configured_replica_salt
from app.core.balancer.selection_helpers import _decorrelated_tie_breaker as _decorrelated_tie_breaker
from app.core.balancer.selection_helpers import _default_replica_salt as _default_replica_salt
from app.core.balancer.selection_helpers import _effective_replica_salt as _effective_replica_salt
from app.core.balancer.selection_helpers import _oldest_due_probing_account as _oldest_due_probing_account
from app.core.balancer.selection_helpers import _process_replica_salt as _process_replica_salt
from app.core.balancer.selection_helpers import _selection_failure as _selection_failure
from app.core.balancer.selection_helpers import _timestamp_as_utc as _timestamp_as_utc
from app.core.balancer.selection_helpers import configure_replica_salt as configure_replica_salt
from app.core.balancer.selection_helpers import pool_usage_exhaustion as pool_usage_exhaustion
from app.core.balancer.state import (
    DEFAULT_RELATIVE_AVAILABILITY_POWER,
    DEFAULT_RELATIVE_AVAILABILITY_TOP_K,
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    ROUTING_POLICY_BURN_FIRST,
    ROUTING_POLICY_NORMAL,
    ROUTING_POLICY_PRESERVE,
    TRAFFIC_CLASS_FOREGROUND,
    TRAFFIC_CLASS_OPPORTUNISTIC,
    AccountState,
    ResetPreferenceWindow,
    RoutingCostsByAccount,
    SelectionResult,
    TrafficClass,
    UsageWeightedOrder,
)
from app.core.balancer.state import RoutingStrategy as RoutingStrategy
from app.db.models import AccountStatus


def select_account(
    states: Iterable[AccountState],
    now: float | None = None,
    *,
    prefer_earlier_reset: bool = False,
    prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    routing_strategy: RoutingStrategy = "capacity_weighted",
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    recovery_probe_only: bool = False,
    relative_availability_power: float = DEFAULT_RELATIVE_AVAILABILITY_POWER,
    relative_availability_top_k: int = DEFAULT_RELATIVE_AVAILABILITY_TOP_K,
    usage_weighted_order: UsageWeightedOrder = "secondary_first",
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
    bypass_quota_exceeded: bool = False,
    bypass_quota_exceeded_account_ids: Collection[str] | None = None,
    primary_first_usage_weighted: bool = False,
    routing_costs: RoutingCostsByAccount | None = None,
    replica_salt: str | None = None,
    allow_usage_exhaustion_error: bool = True,
    usage_exhaustion_states: Iterable[AccountState] | None = None,
) -> SelectionResult:
    current = now or time.time()
    available: list[AccountState] = []
    in_error_backoff: list[AccountState] = []
    cooled: list[AccountState] = []
    all_states = list(states)
    usage_exhaustion_state_list = list(usage_exhaustion_states) if usage_exhaustion_states is not None else all_states
    bypass_account_ids = None if bypass_quota_exceeded_account_ids is None else set(bypass_quota_exceeded_account_ids)
    for state in all_states:
        bypass_standard_quota = (
            ignore_standard_quota
            or state.ignore_standard_quota
            or bypass_quota_exceeded
            or (bypass_account_ids is not None and state.account_id in bypass_account_ids)
        )
        if state.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.EXCHANGE_UNCERTAIN, AccountStatus.DEACTIVATED):
            continue
        if state.status == AccountStatus.PAUSED:
            continue
        if state.status == AccountStatus.RATE_LIMITED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.reset_at = None
            elif not bypass_standard_quota:
                continue
        if state.status == AccountStatus.QUOTA_EXCEEDED:
            if state.reset_at and current >= state.reset_at:
                state.status = AccountStatus.ACTIVE
                state.reset_at = None
            elif not bypass_standard_quota:
                continue
        if state.cooldown_until and current >= state.cooldown_until:
            state.cooldown_until = None
            state.last_error_at = None
            if state.blocked_at is None:
                state.error_count = 0
        if state.cooldown_until and current < state.cooldown_until:
            cooled.append(state)
            continue
        if state.error_count >= 3 and state.blocked_at is None:
            backoff = min(300, 30 * 2 ** (state.error_count - 3))
            if state.last_error_at and current - state.last_error_at < backoff:
                in_error_backoff.append(state)
                continue
            state.error_count = 0
            state.last_error_at = None
        available.append(state)
    if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC and available:
        opportunistic_available, reason = _filter_opportunistic_candidates(available, current)
        if not opportunistic_available:
            return SelectionResult(None, f"opportunistic burn window closed: {reason}")
        available = opportunistic_available
    if not available:
        if allow_backoff_fallback and in_error_backoff:
            available.append(min(in_error_backoff, key=lambda s: (s.error_count, s.last_error_at or 0.0)))
            if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC:
                opportunistic_available, reason = _filter_opportunistic_candidates(available, current)
                if not opportunistic_available:
                    return SelectionResult(None, f"opportunistic burn window closed: {reason}")
                available = opportunistic_available
        else:
            if allow_usage_exhaustion_error:
                usage_exhaustion = pool_usage_exhaustion(
                    usage_exhaustion_state_list,
                    current=current,
                    ignore_standard_quota=ignore_standard_quota or bypass_quota_exceeded,
                    ignore_standard_quota_account_ids=bypass_account_ids,
                )
                if usage_exhaustion is not None:
                    return usage_exhaustion
            reauth_required = [s for s in all_states if s.status == AccountStatus.REAUTH_REQUIRED]
            deactivated = [s for s in all_states if s.status == AccountStatus.DEACTIVATED]
            paused = [s for s in all_states if s.status == AccountStatus.PAUSED]
            rate_limited = [s for s in all_states if s.status == AccountStatus.RATE_LIMITED]
            quota_exceeded = [s for s in all_states if s.status == AccountStatus.QUOTA_EXCEEDED]
            if not rate_limited and (not quota_exceeded):
                if paused and reauth_required and deactivated:
                    return _selection_failure(
                        "All accounts are paused, deactivated, or require re-authentication",
                        all_states,
                        current=current,
                    )
                if paused and reauth_required:
                    return _selection_failure(
                        "All accounts are paused or require re-authentication", all_states, current=current
                    )
                if paused and deactivated:
                    return _selection_failure("All accounts are paused or deactivated", all_states, current=current)
                if reauth_required and deactivated:
                    return _selection_failure(
                        "All accounts are deactivated or require re-authentication", all_states, current=current
                    )
                if paused:
                    return _selection_failure("All accounts are paused", all_states, current=current)
                if reauth_required:
                    return _selection_failure("All accounts require re-authentication", all_states, current=current)
                if deactivated:
                    return _selection_failure("All accounts are deactivated", all_states, current=current)
            if quota_exceeded:
                reset_candidates = [s.reset_at for s in quota_exceeded if s.reset_at]
                if reset_candidates:
                    wait_seconds = max(0, min(reset_candidates) - int(current))
                    return _selection_failure(_format_retry_hint(wait_seconds), all_states, current=current)
            cooldowns = [s.cooldown_until for s in all_states if s.cooldown_until and s.cooldown_until > current]
            if cooldowns:
                wait_seconds = max(0.0, min(cooldowns) - current)
                return _selection_failure(_format_retry_hint(wait_seconds), all_states, current=current)
            if in_error_backoff:
                count = len(in_error_backoff)
                noun = "account" if count == 1 else "accounts"
                return _selection_failure(
                    f"Selector transient backoff excluded {count} {noun}", all_states, current=current
                )
            return _selection_failure("No available accounts", all_states, current=current)

    def _reset_first_sort_key(state: AccountState) -> tuple[int, float, float, float, float, str]:
        reset_bucket_days = _reset_preference_bucket(state, current, prefer_earlier_reset_window)
        if prefer_earlier_reset_window == "primary":
            primary_used, secondary_used, last_selected, account_id = _primary_usage_sort_key(state)
            return (
                reset_bucket_days,
                _planner_cost(state, routing_costs),
                primary_used,
                secondary_used,
                last_selected,
                account_id,
            )
        secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
        return (
            reset_bucket_days,
            _planner_cost(state, routing_costs),
            secondary_used,
            primary_used,
            last_selected,
            account_id,
        )

    def _primary_reset_first_sort_key(state: AccountState) -> tuple[int, float, float, float, float, str]:
        reset_bucket_days = _reset_preference_bucket(state, current, prefer_earlier_reset_window)
        primary_used, secondary_used, last_selected, account_id = _primary_usage_sort_key(state)
        return (
            reset_bucket_days,
            _planner_cost(state, routing_costs),
            primary_used,
            secondary_used,
            last_selected,
            account_id,
        )

    def _primary_usage_sort_key_with_cost(state: AccountState) -> tuple[float, float, float, float, str]:
        primary_used, secondary_used, last_selected, account_id = _primary_usage_sort_key(state)
        return (_planner_cost(state, routing_costs), primary_used, secondary_used, last_selected, account_id)

    def _usage_sort_key_with_cost(state: AccountState) -> tuple[float, float, float, float, str]:
        secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
        return (_planner_cost(state, routing_costs), secondary_used, primary_used, last_selected, account_id)

    round_robin_salt = _effective_replica_salt(replica_salt)

    def _round_robin_sort_key(state: AccountState) -> tuple[float, float, str]:
        return (
            _planner_cost(state, routing_costs),
            state.last_selected_at or 0.0,
            _decorrelated_tie_breaker(state.account_id, round_robin_salt),
        )

    if routing_strategy == "single_account":
        selected = min(available, key=lambda state: state.account_id)
        return SelectionResult(selected, None)
    if routing_strategy == "sequential_drain":
        candidate_pool = [state for state in available if not _usage_exhausted(state)]
        if not candidate_pool:
            return _selection_failure("No available accounts", all_states, current=current)
        selected = min(candidate_pool, key=_sequential_drain_sort_key)
        return SelectionResult(selected, None)
    if routing_strategy == "reset_drain":
        candidate_pool = [state for state in available if not _usage_exhausted(state)]
        if not candidate_pool:
            return _selection_failure("No available accounts", all_states, current=current)
        selected = min(candidate_pool, key=lambda state: _reset_drain_sort_key(state, current))
        return SelectionResult(selected, None)
    healthy = [s for s in available if s.health_tier == HEALTH_TIER_HEALTHY]
    probing = [s for s in available if s.health_tier == HEALTH_TIER_PROBING]
    draining = [s for s in available if s.health_tier == HEALTH_TIER_DRAINING]
    due_probe = _oldest_due_probing_account(probing, current=current) if healthy or recovery_probe_only else None
    if recovery_probe_only and due_probe is None:
        return SelectionResult(None, None)
    health_pool = [due_probe] if due_probe is not None else healthy or probing or draining or available
    burn_first = [s for s in health_pool if _routing_policy(s) == ROUTING_POLICY_BURN_FIRST]
    normal = [s for s in health_pool if _routing_policy(s) == ROUTING_POLICY_NORMAL]
    preserve = [s for s in health_pool if _routing_policy(s) == ROUTING_POLICY_PRESERVE]
    effective_pool = burn_first or normal or preserve or health_pool
    effective_prefer_earlier_reset = prefer_earlier_reset and routing_strategy != "relative_availability"
    if routing_strategy == "round_robin":
        selected = min(effective_pool, key=_round_robin_sort_key)
    elif routing_strategy == "capacity_weighted":
        candidate_pool = (
            _prefer_earlier_reset_candidates(effective_pool, current, prefer_earlier_reset_window)
            if effective_prefer_earlier_reset
            else effective_pool
        )
        if deterministic_probe:
            selected = min(candidate_pool, key=lambda state: _capacity_probe_sort_key_with_cost(state, routing_costs))
        else:
            candidate_pool = _lowest_planner_cost_candidates(candidate_pool, routing_costs)
            selected = _select_capacity_weighted(candidate_pool)
    elif routing_strategy == "relative_availability":
        candidate_pool = _lowest_planner_cost_candidates(effective_pool, routing_costs)
        selected = _select_relative_availability(
            candidate_pool,
            current=current,
            power=relative_availability_power,
            top_k=relative_availability_top_k,
            deterministic_probe=deterministic_probe,
        )
    elif routing_strategy == "fill_first":
        candidate_pool = (
            _prefer_earlier_reset_candidates(effective_pool, current, prefer_earlier_reset_window)
            if prefer_earlier_reset
            else effective_pool
        )
        selected = _select_fill_first(candidate_pool)
    else:
        effective_usage_weighted_order: UsageWeightedOrder = (
            "primary_first" if primary_first_usage_weighted else usage_weighted_order
        )
        if effective_usage_weighted_order == "primary_first":
            selected = min(
                effective_pool,
                key=_primary_reset_first_sort_key
                if effective_prefer_earlier_reset
                else _primary_usage_sort_key_with_cost,
            )
        else:
            selected = min(
                effective_pool,
                key=_reset_first_sort_key if effective_prefer_earlier_reset else _usage_sort_key_with_cost,
            )
    return SelectionResult(selected, None)
