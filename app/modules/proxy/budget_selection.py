from __future__ import annotations

from typing import Iterable

from app.core.balancer import (
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    ROUTING_POLICY_BURN_FIRST,
    ROUTING_POLICY_PRESERVE,
    TRAFFIC_CLASS_FOREGROUND,
    AccountState,
    ResetPreferenceWindow,
    RoutingCostsByAccount,
    RoutingStrategy,
    SelectionResult,
    TrafficClass,
    select_account,
)


def state_above_budget_threshold(state: AccountState, budget_threshold_pct: float) -> bool:
    used_percent = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    return used_percent is not None and used_percent > budget_threshold_pct


def state_above_sticky_budget_threshold(
    state: AccountState,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float | None = None,
) -> bool:
    secondary_threshold = (
        budget_threshold_pct if secondary_budget_threshold_pct is None else secondary_budget_threshold_pct
    )
    used_percent = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    if state.limit_scoped_usage and state.priority_secondary_used_percent is None:
        secondary_used_percent = used_percent
    else:
        secondary_used_percent = (
            state.priority_secondary_used_percent
            if state.priority_secondary_used_percent is not None
            else state.secondary_used_percent
        )
    return (used_percent is not None and used_percent > budget_threshold_pct) or (
        secondary_used_percent is not None and secondary_used_percent > secondary_threshold
    )


def best_health_tier_states(states: list[AccountState]) -> list[AccountState]:
    healthy = [state for state in states if state.health_tier == HEALTH_TIER_HEALTHY]
    if healthy:
        return healthy
    probing = [state for state in states if state.health_tier == HEALTH_TIER_PROBING]
    if probing:
        return probing
    draining = [state for state in states if state.health_tier == HEALTH_TIER_DRAINING]
    return draining or states


def select_account_preferring_budget_safe(
    states: Iterable[AccountState],
    *,
    prefer_earlier_reset: bool,
    prefer_earlier_reset_window: ResetPreferenceWindow = "secondary",
    routing_strategy: RoutingStrategy,
    relative_availability_power: float = 2.0,
    relative_availability_top_k: int = 5,
    budget_threshold_pct: float,
    secondary_budget_threshold_pct: float = 100.0,
    apply_secondary_budget_threshold: bool = False,
    allow_backoff_fallback: bool = True,
    deterministic_probe: bool = False,
    traffic_class: TrafficClass = TRAFFIC_CLASS_FOREGROUND,
    allow_personal_fallback: bool = True,
    ignore_standard_quota: bool = False,
    routing_costs_by_account_id: RoutingCostsByAccount | None = None,
) -> SelectionResult:
    state_list = list(states)
    state_budget_threshold = (
        (
            lambda state: state_above_sticky_budget_threshold(
                state,
                budget_threshold_pct,
                secondary_budget_threshold_pct,
            )
        )
        if apply_secondary_budget_threshold
        else (lambda state: state_above_budget_threshold(state, budget_threshold_pct))
    )
    if routing_strategy in ("sequential_drain", "reset_drain", "single_account"):
        budget_safe_states = [
            state
            for state in state_list
            if state.routing_policy != ROUTING_POLICY_PRESERVE and not state_budget_threshold(state)
        ]
        return select_account(
            budget_safe_states or state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            allow_personal_fallback=allow_personal_fallback,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
        )
    best_health_states = best_health_tier_states(state_list)
    burn_first_states = [state for state in best_health_states if state.routing_policy == ROUTING_POLICY_BURN_FIRST]
    if burn_first_states:
        burn_first = select_account(
            burn_first_states,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=False,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            allow_personal_fallback=allow_personal_fallback,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
        )
        if burn_first.account is not None:
            return burn_first
    preferred_states = [
        state
        for state in state_list
        if state.routing_policy != ROUTING_POLICY_PRESERVE and not state_budget_threshold(state)
    ]
    if preferred_states:
        selection_pool = preferred_states if len(preferred_states) != len(state_list) else state_list
        preferred = select_account(
            selection_pool,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            relative_availability_power=relative_availability_power,
            relative_availability_top_k=relative_availability_top_k,
            traffic_class=traffic_class,
            allow_personal_fallback=allow_personal_fallback,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
        )
        if preferred.account is not None:
            return preferred
        if len(preferred_states) == len(state_list):
            return preferred
    if routing_strategy == "usage_weighted" and state_list:
        return select_account(
            state_list,
            prefer_earlier_reset=prefer_earlier_reset,
            prefer_earlier_reset_window=prefer_earlier_reset_window,
            routing_strategy=routing_strategy,
            allow_backoff_fallback=allow_backoff_fallback,
            deterministic_probe=deterministic_probe,
            usage_weighted_order="primary_first",
            traffic_class=traffic_class,
            allow_personal_fallback=allow_personal_fallback,
            ignore_standard_quota=ignore_standard_quota,
            routing_costs=routing_costs_by_account_id,
        )
    return select_account(
        state_list,
        prefer_earlier_reset=prefer_earlier_reset,
        prefer_earlier_reset_window=prefer_earlier_reset_window,
        routing_strategy=routing_strategy,
        allow_backoff_fallback=allow_backoff_fallback,
        deterministic_probe=deterministic_probe,
        relative_availability_power=relative_availability_power,
        relative_availability_top_k=relative_availability_top_k,
        traffic_class=traffic_class,
        allow_personal_fallback=allow_personal_fallback,
        ignore_standard_quota=ignore_standard_quota,
        routing_costs=routing_costs_by_account_id,
    )
