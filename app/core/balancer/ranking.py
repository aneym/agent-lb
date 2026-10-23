from __future__ import annotations

import hashlib
import logging
import random

from app.core.balancer.state import (
    CAPACITY_PLAN_ALIASES,
    DEFAULT_RELATIVE_AVAILABILITY_POWER,
    RELATIVE_AVAILABILITY_MIN_DIVISOR_SECONDS,
    RELATIVE_AVAILABILITY_MIN_WEIGHT_FRACTION,
    SECONDS_PER_DAY,
    UNKNOWN_PLAN_FALLBACK,
    UNKNOWN_RESET_BUCKET_DAYS,
    UNKNOWN_RESET_FALLBACK_SECONDS,
    AccountState,
    ResetPreferenceWindow,
    RoutingCostsByAccount,
)
from app.core.usage import PLAN_CAPACITY_CREDITS_SECONDARY

logger = logging.getLogger("app.core.balancer.logic")
_RELATIVE_AVAILABILITY_LOG_PREFIX_CANDIDATE = "Relative availability candidate "
_RELATIVE_AVAILABILITY_LOG_PREFIX_TOP_K = "Relative availability top-k     "
_RELATIVE_AVAILABILITY_LOG_PREFIX_WINNER = "Relative availability winner    "


def _planner_cost(state: AccountState, routing_costs: RoutingCostsByAccount | None) -> float:
    if not routing_costs:
        return 0.0
    cost = routing_costs.get(state.account_id)
    if cost is None:
        return 0.0
    return float(cost.total)


def _usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
    primary_used = _priority_primary_used(state)
    secondary_used = _priority_secondary_used(state, primary_used)
    last_selected = state.last_selected_at or 0.0
    return (secondary_used, primary_used, last_selected, state.account_id)


def _primary_usage_sort_key(state: AccountState) -> tuple[float, float, float, str]:
    primary_used = _priority_primary_used(state)
    secondary_used = _priority_secondary_used(state, primary_used)
    last_selected = state.last_selected_at or 0.0
    return (primary_used, secondary_used, last_selected, state.account_id)


def _reset_preference_bucket(state: AccountState, current: float, window: ResetPreferenceWindow) -> int:
    if window == "primary":
        reset_at = state.primary_reset_at
        if reset_at is None:
            reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
    else:
        reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
        if reset_at is None:
            reset_at = state.primary_reset_at
    if reset_at is None:
        return UNKNOWN_RESET_BUCKET_DAYS * SECONDS_PER_DAY
    remaining_seconds = max(0, int(reset_at - current))
    if window == "secondary":
        return remaining_seconds // SECONDS_PER_DAY
    return remaining_seconds


def _prefer_earlier_reset_candidates(
    available: list[AccountState], current: float, window: ResetPreferenceWindow
) -> list[AccountState]:
    earliest_bucket = min((_reset_preference_bucket(state, current, window) for state in available))
    return [state for state in available if _reset_preference_bucket(state, current, window) == earliest_bucket]


def _fallback_secondary_capacity_credits(plan_type: str | None) -> float:
    normalized = (plan_type or "").strip().lower()
    resolved_plan = CAPACITY_PLAN_ALIASES.get(normalized, normalized or UNKNOWN_PLAN_FALLBACK)
    return PLAN_CAPACITY_CREDITS_SECONDARY.get(resolved_plan, PLAN_CAPACITY_CREDITS_SECONDARY[UNKNOWN_PLAN_FALLBACK])


def _remaining_secondary_credits(state: AccountState) -> float:
    capacity = (
        state.priority_capacity_credits if state.priority_capacity_credits is not None else state.capacity_credits
    )
    if capacity is None:
        capacity = _fallback_secondary_capacity_credits(state.plan_type)
    elif capacity <= 0:
        return 0.0
    primary_used = _priority_primary_used(state)
    used_pct = _priority_secondary_used(state, primary_used)
    return max(0.0, capacity * (1.0 - min(used_pct, 100.0) / 100.0))


def _priority_primary_used(state: AccountState) -> float:
    value = state.priority_used_percent if state.priority_used_percent is not None else state.used_percent
    return value if value is not None else 0.0


def _priority_secondary_used(state: AccountState, primary_used: float | None = None) -> float:
    if state.limit_scoped_usage and state.priority_secondary_used_percent is None:
        return primary_used if primary_used is not None else _priority_primary_used(state)
    value = (
        state.priority_secondary_used_percent
        if state.priority_secondary_used_percent is not None
        else state.secondary_used_percent
    )
    if value is not None:
        return value
    return primary_used if primary_used is not None else _priority_primary_used(state)


def _relative_availability_divisor_seconds(state: AccountState, current: float) -> float:
    reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
    if reset_at is None:
        remaining_seconds = float(UNKNOWN_RESET_FALLBACK_SECONDS)
    else:
        remaining_seconds = max(0.0, float(reset_at) - current)
    return max(remaining_seconds, float(RELATIVE_AVAILABILITY_MIN_DIVISOR_SECONDS))


def _relative_availability_remaining_seconds(state: AccountState, current: float) -> float:
    reset_at = state.priority_reset_at if state.priority_reset_at is not None else state.secondary_reset_at
    if reset_at is None:
        return float(UNKNOWN_RESET_FALLBACK_SECONDS)
    return max(0.0, float(reset_at) - current)


def _relative_availability_raw_score(state: AccountState, current: float) -> float:
    remaining_credits = _remaining_secondary_credits(state)
    if remaining_credits <= 0.0:
        return 0.0
    return remaining_credits / _relative_availability_divisor_seconds(state, current)


def _relative_availability_label(state: AccountState) -> str:
    return state.account_id


def _relative_availability_score_per_minute(raw_score: float) -> float:
    return raw_score * 60.0


def _log_relative_availability_candidate_scores(
    raw_scores: list[tuple[AccountState, float]], *, current: float
) -> None:
    for state, raw_score in raw_scores:
        remaining_seconds = _relative_availability_remaining_seconds(state, current)
        logger.debug(
            f"{_RELATIVE_AVAILABILITY_LOG_PREFIX_CANDIDATE}account=%s remaining_credits=%.2f remaining_minutes=%.2f score_per_minute=%.6f",
            _relative_availability_label(state),
            _remaining_secondary_credits(state),
            remaining_seconds / 60.0,
            _relative_availability_score_per_minute(raw_score),
        )


def _log_relative_availability_top_k(
    weighted_candidates: list[tuple[AccountState, float, float]], *, current: float
) -> None:
    formatted_candidates = ", ".join(
        (
            f"account={_relative_availability_label(state)} remaining_credits={_remaining_secondary_credits(state):.2f} remaining_minutes={_relative_availability_remaining_seconds(state, current) / 60.0:.2f} score_per_minute={_relative_availability_score_per_minute(raw_score):.6f} weight={weight:.8f}"
            for state, weight, raw_score in weighted_candidates
        )
    )
    logger.info("%s%s", _RELATIVE_AVAILABILITY_LOG_PREFIX_TOP_K, formatted_candidates)


def _relative_availability_weighted_candidates(
    available: list[AccountState], *, current: float, power: float, top_k: int
) -> list[tuple[AccountState, float, float]]:
    raw_scores = [(state, _relative_availability_raw_score(state, current)) for state in available]
    _log_relative_availability_candidate_scores(raw_scores, current=current)
    best_raw_score = max((score for _, score in raw_scores), default=0.0)
    if best_raw_score <= 0.0:
        return []
    weighted: list[tuple[AccountState, float, float]] = []
    safe_power = power if power > 0.0 else DEFAULT_RELATIVE_AVAILABILITY_POWER
    for state, raw_score in raw_scores:
        normalized_score = raw_score / best_raw_score
        weight = normalized_score**safe_power
        if weight < RELATIVE_AVAILABILITY_MIN_WEIGHT_FRACTION:
            continue
        weighted.append((state, weight, raw_score))
    if not weighted:
        return []
    weighted.sort(key=lambda item: (-item[1], -item[2], *_usage_sort_key(item[0])))
    safe_top_k = max(1, top_k)
    top_candidates = weighted[:safe_top_k]
    _log_relative_availability_top_k(top_candidates, current=current)
    return top_candidates


def _log_relative_availability_winner(
    winner: AccountState, *, current: float, weight: float | None, raw_score: float
) -> None:
    remaining_seconds = _relative_availability_remaining_seconds(winner, current)
    logger.info(
        f"{_RELATIVE_AVAILABILITY_LOG_PREFIX_WINNER}account=%s remaining_credits=%.2f remaining_minutes=%.2f score_per_minute=%.6f weight=%s",
        _relative_availability_label(winner),
        _remaining_secondary_credits(winner),
        remaining_seconds / 60.0,
        _relative_availability_score_per_minute(raw_score),
        f"{weight:.8f}" if weight is not None else "fallback",
    )


def _select_relative_availability(
    available: list[AccountState], *, current: float, power: float, top_k: int, deterministic_probe: bool
) -> AccountState:
    weighted_candidates = _relative_availability_weighted_candidates(
        available, current=current, power=power, top_k=top_k
    )
    if not weighted_candidates:
        winner = min(available, key=_usage_sort_key)
        _log_relative_availability_winner(
            winner, current=current, weight=None, raw_score=_relative_availability_raw_score(winner, current)
        )
        return winner
    if deterministic_probe:
        winner, weight, raw_score = weighted_candidates[0]
        _log_relative_availability_winner(winner, current=current, weight=weight, raw_score=raw_score)
        return winner
    states = [state for state, _, _ in weighted_candidates]
    weights = [weight for _, weight, _ in weighted_candidates]
    total = sum(weights)
    if total <= 0.0:
        winner = min(available, key=_usage_sort_key)
        _log_relative_availability_winner(
            winner, current=current, weight=None, raw_score=_relative_availability_raw_score(winner, current)
        )
        return winner
    winner = random.choices(states, weights=weights, k=1)[0]
    for state, weight, raw_score in weighted_candidates:
        if state.account_id == winner.account_id:
            _log_relative_availability_winner(winner, current=current, weight=weight, raw_score=raw_score)
            break
    return winner


def _stable_tie_breaker(account_id: str) -> str:
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _configured_capacity_credits(state: AccountState) -> float:
    if state.capacity_credits is not None and state.capacity_credits > 0:
        return state.capacity_credits
    return _fallback_secondary_capacity_credits(state.plan_type)


def _usage_exhausted(state: AccountState) -> bool:
    primary_raw = state.raw_used_percent if state.raw_used_percent is not None else state.used_percent
    secondary_raw = (
        state.raw_secondary_used_percent
        if state.raw_secondary_used_percent is not None
        else state.secondary_used_percent
    )
    primary_used = primary_raw if primary_raw is not None else 0.0
    secondary_used = secondary_raw if secondary_raw is not None else primary_used
    return primary_used >= 100.0 or secondary_used >= 100.0


def _sequential_drain_sort_key(state: AccountState) -> tuple[float, str, str]:
    return (_configured_capacity_credits(state), _stable_tie_breaker(state.account_id), state.account_id)


def _weekly_reset_timestamp(state: AccountState, current: float) -> float:
    if state.secondary_reset_at is not None and float(state.secondary_reset_at) > current:
        return float(state.secondary_reset_at)
    if state.reset_at is not None and float(state.reset_at) > current:
        return float(state.reset_at)
    return float("inf")


def _reset_drain_sort_key(state: AccountState, current: float) -> tuple[int, float, float, float, str, str]:
    primary_remaining = 100.0 - (state.used_percent if state.used_percent is not None else 0.0)
    secondary_remaining = 100.0 - (
        state.secondary_used_percent
        if state.secondary_used_percent is not None
        else state.used_percent
        if state.used_percent is not None
        else 0.0
    )
    reset_at = _weekly_reset_timestamp(state, current)
    reset_bucket_days = (
        UNKNOWN_RESET_BUCKET_DAYS if reset_at == float("inf") else max(0, int((reset_at - current) // SECONDS_PER_DAY))
    )
    return (
        reset_bucket_days,
        -max(0.0, primary_remaining),
        -max(0.0, secondary_remaining),
        reset_at,
        _stable_tie_breaker(state.account_id),
        state.account_id,
    )


def _capacity_probe_sort_key_with_cost(
    state: AccountState, routing_costs: RoutingCostsByAccount | None
) -> tuple[float, float, float, float, float, str]:
    secondary_used, primary_used, last_selected, account_id = _usage_sort_key(state)
    return (
        _planner_cost(state, routing_costs),
        -_remaining_secondary_credits(state),
        secondary_used,
        primary_used,
        last_selected,
        account_id,
    )


def _lowest_planner_cost_candidates(
    available: list[AccountState], routing_costs: RoutingCostsByAccount | None
) -> list[AccountState]:
    if not routing_costs:
        return available
    lowest_cost = min((_planner_cost(state, routing_costs) for state in available))
    return [state for state in available if _planner_cost(state, routing_costs) == lowest_cost]


def _select_capacity_weighted(available: list[AccountState]) -> AccountState:
    weights = [_remaining_secondary_credits(s) for s in available]
    total = sum(weights)
    if total <= 0.0:
        return min(available, key=_usage_sort_key)
    return random.choices(available, weights=weights, k=1)[0]


def _fill_first_sort_key(state: AccountState) -> tuple[float, float, str]:
    primary_used = state.used_percent if state.used_percent is not None else 0.0
    secondary_used = state.secondary_used_percent if state.secondary_used_percent is not None else 0.0
    return (-primary_used, -secondary_used, state.account_id)


def _select_fill_first(available: list[AccountState]) -> AccountState:
    return min(available, key=_fill_first_sort_key)
