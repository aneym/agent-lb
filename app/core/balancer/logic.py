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
from app.core.balancer.tiers import shared_first
from app.db.models import AccountStatus


def _timestamp_as_utc(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _account_quota_reset_at(state: AccountState, current: float) -> float | None:
    if state.status not in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED):
        return None
    candidates: list[float] = []
    if state.reset_at is not None and state.reset_at > current:
        candidates.append(float(state.reset_at))
    primary_used = state.raw_used_percent if state.raw_used_percent is not None else state.used_percent
    secondary_used = (
        state.raw_secondary_used_percent
        if state.raw_secondary_used_percent is not None
        else state.secondary_used_percent
    )
    if (
        primary_used is not None
        and primary_used >= 100.0
        and state.primary_reset_at is not None
        and state.primary_reset_at > current
    ):
        candidates.append(float(state.primary_reset_at))
    if (
        secondary_used is not None
        and secondary_used >= 100.0
        and state.secondary_reset_at is not None
        and state.secondary_reset_at > current
    ):
        candidates.append(float(state.secondary_reset_at))
    if not candidates:
        fallback = (
            state.reset_at if state.reset_at is not None else (state.primary_reset_at or state.secondary_reset_at)
        )
        if fallback is not None and fallback > current:
            candidates.append(float(fallback))
    return max(candidates) if candidates else None


def _account_transient_backoff_until(state: AccountState) -> float | None:
    if state.error_count >= 3 and state.blocked_at is None and state.last_error_at is not None:
        return state.last_error_at + min(300, 30 * (2 ** (state.error_count - 3)))
    return None


def _account_cooldown_until(state: AccountState, current: float) -> float | None:
    future = [
        float(ts) for ts in (state.error_rule_cooldown_until, state.cooldown_until) if ts is not None and ts > current
    ]
    if future:
        return max(future)
    return state.error_rule_cooldown_until or state.cooldown_until


def _account_recovery_at(state: AccountState, current: float) -> float | None:
    if state.status in (
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.EXCHANGE_UNCERTAIN,
        AccountStatus.DEACTIVATED,
        AccountStatus.PAUSED,
    ):
        return None
    blockers = [
        float(ts)
        for ts in (
            _account_quota_reset_at(state, current),
            state.error_rule_cooldown_until,
            state.cooldown_until,
            _account_transient_backoff_until(state),
        )
        if ts is not None and ts > current
    ]
    return max(blockers) if blockers else None


def _selection_failure(
    message: str,
    states: Iterable[AccountState],
    *,
    current: float,
) -> SelectionResult:
    state_list = list(states)
    excluded_accounts = [
        ExcludedAccount(
            account_id=state.account_id,
            status=state.status.value,
            reset_at=_timestamp_as_utc(
                _account_quota_reset_at(state, current)
                if state.status in (AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)
                else (state.reset_at if state.reset_at is not None else state.primary_reset_at)
            ),
            cooldown_until=_timestamp_as_utc(_account_cooldown_until(state, current)),
            deactivation_reason=state.deactivation_reason,
            plan_type=state.plan_type,
            transient_backoff_until=_timestamp_as_utc(_account_transient_backoff_until(state)),
        )
        for state in state_list
    ]
    future_recoveries = [
        _timestamp_as_utc(recovery_ts)
        for state in state_list
        if (recovery_ts := _account_recovery_at(state, current)) is not None
    ]
    return SelectionResult(
        account=None,
        error_message=message,
        excluded_accounts=excluded_accounts,
        retry_at=min((ts for ts in future_recoveries if ts is not None), default=None),
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
    allow_personal_fallback: bool = True,
) -> SelectionResult:

    current = now or time.time()
    available: list[AccountState] = []
    in_error_backoff: list[AccountState] = []
    cooled: list[AccountState] = []
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
        if state.error_rule_cooldown_until is not None:
            if current < state.error_rule_cooldown_until:
                continue
            state.error_rule_cooldown_until = None
        if state.cooldown_until and current >= state.cooldown_until:
            state.cooldown_until = None
            state.last_error_at = None
            if state.blocked_at is None:
                state.error_count = 0
        if state.cooldown_until and current < state.cooldown_until:
            cooled.append(state)
            continue
        if state.error_count >= 3 and state.blocked_at is None:
            backoff = min(300, 30 * (2 ** (state.error_count - 3)))
            if state.last_error_at and current - state.last_error_at < backoff:
                in_error_backoff.append(state)
                continue

            state.error_count = 0
            state.last_error_at = None
        available.append(state)

    personal_isolated = False
    if not allow_personal_fallback and available:
        available = shared_first(available, allow_personal_fallback=False)
        personal_isolated = not available

    if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC and available:
        opportunistic_available, reason = _filter_opportunistic_candidates(available, current)
        if not opportunistic_available:
            return SelectionResult(None, f"opportunistic burn window closed: {reason}")
        available = opportunistic_available

    if not available:
        backoff_pool = shared_first(
            in_error_backoff + cooled,
            allow_personal_fallback=allow_personal_fallback,
        )
        if allow_backoff_fallback and backoff_pool:
            available.append(min(backoff_pool, key=lambda s: (s.error_count, s.last_error_at or 0.0)))
            if traffic_class == TRAFFIC_CLASS_OPPORTUNISTIC:
                opportunistic_available, reason = _filter_opportunistic_candidates(available, current)
                if not opportunistic_available:
                    return SelectionResult(None, f"opportunistic burn window closed: {reason}")
                available = opportunistic_available
        elif personal_isolated or (not allow_personal_fallback and (in_error_backoff or cooled) and not backoff_pool):
            return _selection_failure(
                "No shared accounts available (personal accounts isolated from caller)",
                all_states,
                current=current,
            )
        else:
            reauth_required = [s for s in all_states if s.status == AccountStatus.REAUTH_REQUIRED]
            deactivated = [s for s in all_states if s.status == AccountStatus.DEACTIVATED]
            paused = [s for s in all_states if s.status == AccountStatus.PAUSED]
            rate_limited = [s for s in all_states if s.status == AccountStatus.RATE_LIMITED]
            quota_exceeded = [s for s in all_states if s.status == AccountStatus.QUOTA_EXCEEDED]
            recoveries = [recovery for s in all_states if (recovery := _account_recovery_at(s, current)) is not None]

            if not rate_limited and not quota_exceeded and not recoveries and not in_error_backoff:
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
            if recoveries:
                return _selection_failure(_format_retry_hint(min(recoveries), current), all_states, current=current)
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
    health_pool = shared_first(
        healthy or probing or draining or available,
        allow_personal_fallback=allow_personal_fallback,
    )
    if not health_pool:
        return _selection_failure(
            "No shared accounts available (personal accounts isolated from caller)",
            all_states,
            current=current,
        )
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
