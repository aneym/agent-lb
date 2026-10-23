from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Collection, Iterable

from app.core.balancer.failures import _format_retry_hint
from app.core.balancer.opportunistic import _filter_opportunistic_candidates, _routing_policy
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
    ExcludedAccount,
    ResetPreferenceWindow,
    RoutingCostsByAccount,
    RoutingStrategy,
    SelectionResult,
    TrafficClass,
    UsageWeightedOrder,
)
from app.db.models import AccountStatus


def _timestamp_as_utc(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _selection_failure(
    message: str,
    states: Iterable[AccountState],
    *,
    current: float,
) -> SelectionResult:
    excluded_accounts = [
        ExcludedAccount(
            account_id=state.account_id,
            status=state.status.value,
            reset_at=_timestamp_as_utc(state.reset_at if state.reset_at is not None else state.primary_reset_at),
            cooldown_until=_timestamp_as_utc(state.cooldown_until),
            deactivation_reason=state.deactivation_reason,
            plan_type=state.plan_type,
            transient_backoff_until=_timestamp_as_utc(
                state.last_error_at + min(300, 30 * (2 ** (state.error_count - 3)))
                if state.error_count >= 3 and state.blocked_at is None and state.last_error_at is not None
                else None
            ),
        )
        for state in states
    ]
    future_recoveries = [
        recovery_at
        for excluded in excluded_accounts
        for recovery_at in (
            excluded.reset_at if excluded.status in ("rate_limited", "quota_exceeded") else None,
            excluded.cooldown_until,
            excluded.transient_backoff_until,
        )
        if recovery_at is not None and recovery_at.timestamp() > current
    ]
    return SelectionResult(
        account=None,
        error_message=message,
        excluded_accounts=excluded_accounts,
        retry_at=min(future_recoveries, default=None),
    )


def select_account(
    states: Iterable[AccountState],
    now: float | None = None,
    *,
    prefer_earlier_reset: bool = False,
    prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    routing_strategy: RoutingStrategy = "capacity_weighted",
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    relative_availability_power: float = DEFAULT_RELATIVE_AVAILABILITY_POWER,
    relative_availability_top_k: int = DEFAULT_RELATIVE_AVAILABILITY_TOP_K,
    usage_weighted_order: UsageWeightedOrder = "secondary_first",
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    ignore_standard_quota: bool = False,
    bypass_quota_exceeded: bool = False,
    bypass_quota_exceeded_account_ids: Collection[str] | None = None,
    primary_first_usage_weighted: bool = False,
    routing_costs: RoutingCostsByAccount | None = None,
) -> SelectionResult:

    current = now or time.time()
    available: list[AccountState] = []
    in_error_backoff: list[AccountState] = []
    all_states = list(states)
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
            continue
        if state.error_count >= 3 and state.blocked_at is None:
            backoff = min(300, 30 * (2 ** (state.error_count - 3)))
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
            reauth_required = [s for s in all_states if s.status == AccountStatus.REAUTH_REQUIRED]
            deactivated = [s for s in all_states if s.status == AccountStatus.DEACTIVATED]
            paused = [s for s in all_states if s.status == AccountStatus.PAUSED]
            rate_limited = [s for s in all_states if s.status == AccountStatus.RATE_LIMITED]
            quota_exceeded = [s for s in all_states if s.status == AccountStatus.QUOTA_EXCEEDED]

            if not rate_limited and not quota_exceeded:
                if paused and reauth_required and deactivated:
                    return _selection_failure(
                        "All accounts are paused, deactivated, or require re-authentication",
                        all_states,
                        current=current,
                    )
                if paused and reauth_required:
                    return _selection_failure(
                        "All accounts are paused or require re-authentication",
                        all_states,
                        current=current,
                    )
                if paused and deactivated:
                    return _selection_failure(
                        "All accounts are paused or deactivated",
                        all_states,
                        current=current,
                    )
                if reauth_required and deactivated:
                    return _selection_failure(
                        "All accounts are deactivated or require re-authentication",
                        all_states,
                        current=current,
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
        return _planner_cost(state, routing_costs), primary_used, secondary_used, last_selected, account_id

    def _usage_sort_key_with_cost(state: AccountState) -> tuple[float, float, float, float, str]:
        secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
        return _planner_cost(state, routing_costs), secondary_used, primary_used, last_selected, account_id

    def _round_robin_sort_key(state: AccountState) -> tuple[float, float, str]:

        return _planner_cost(state, routing_costs), state.last_selected_at or 0.0, state.account_id

    if routing_strategy == "single_account":
        candidate_pool = [state for state in available if not _usage_exhausted(state)]
        if not candidate_pool:
            return SelectionResult(None, "Selected account is exhausted or unavailable")
        selected = min(candidate_pool, key=lambda state: state.account_id)
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
    health_pool = healthy or probing or draining or available
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
                key=(
                    _primary_reset_first_sort_key
                    if effective_prefer_earlier_reset
                    else _primary_usage_sort_key_with_cost
                ),
            )
        else:
            selected = min(
                effective_pool,
                key=_reset_first_sort_key if effective_prefer_earlier_reset else _usage_sort_key_with_cost,
            )
    return SelectionResult(selected, None)
