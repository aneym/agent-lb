from __future__ import annotations

from app.core.balancer.state import (
    NORMAL_LAST_ACCOUNT_EMERGENCY_FLOOR_PCT,
    PRESERVE_MIN_SHORT_WINDOW_FLOOR_PCT,
    PRESERVE_MIN_WEEKLY_FLOOR_PCT,
    RECENT_FOREGROUND_ACTIVITY_SECONDS,
    ROUTING_POLICY_BURN_FIRST,
    ROUTING_POLICY_NORMAL,
    ROUTING_POLICY_PRESERVE,
    SECONDS_PER_DAY,
    SECONDS_PER_HOUR,
    SECONDS_PER_WEEK,
    AccountState,
)
from app.db.models import AccountStatus


def _routing_policy(state: AccountState) -> str:
    if state.routing_policy in {ROUTING_POLICY_BURN_FIRST, ROUTING_POLICY_NORMAL, ROUTING_POLICY_PRESERVE}:
        return state.routing_policy
    return ROUTING_POLICY_NORMAL


def _used_pct(state: AccountState, *, secondary: bool) -> float | None:
    if secondary:
        return state.secondary_used_percent if state.secondary_used_percent is not None else state.used_percent
    return state.used_percent


def _remaining_pct(state: AccountState, *, secondary: bool) -> float | None:
    used_pct = _used_pct(state, secondary=secondary)
    if used_pct is None:
        return None
    return max(0.0, 100.0 - min(100.0, used_pct))


def _seconds_until(reset_at: int | float | None, current: float) -> float | None:
    if reset_at is None:
        return None
    return max(0.0, float(reset_at) - current)


def _recent_foreground_activity(state: AccountState, current: float) -> bool:
    return state.last_selected_at is not None and current - state.last_selected_at <= RECENT_FOREGROUND_ACTIVITY_SECONDS


def _weekly_pace_floor_pct(state: AccountState, current: float) -> float:
    remaining_seconds = _seconds_until(state.secondary_reset_at, current)
    used_pct = _used_pct(state, secondary=True)
    if remaining_seconds is None or used_pct is None:
        return 100.0
    elapsed_seconds = max(0.0, SECONDS_PER_WEEK - remaining_seconds)
    expected_used_pct = min(100.0, elapsed_seconds / SECONDS_PER_WEEK * 100.0)
    behind_pace = used_pct + 5.0 < expected_used_pct
    if remaining_seconds <= 6 * SECONDS_PER_HOUR and behind_pace:
        pace_floor = 0.0
    elif remaining_seconds <= SECONDS_PER_DAY and behind_pace:
        pace_floor = 2.0
    elif behind_pace:
        pace_floor = 5.0
    else:
        pace_floor = 15.0
    if _recent_foreground_activity(state, current):
        pace_floor = max(pace_floor, 25.0)
    return max(PRESERVE_MIN_WEEKLY_FLOOR_PCT, pace_floor)


def _short_window_floor_pct(state: AccountState, current: float, *, preserve_count: int) -> float:
    remaining_seconds = _seconds_until(state.reset_at, current)
    floor = PRESERVE_MIN_SHORT_WINDOW_FLOOR_PCT
    if remaining_seconds is None:
        return 100.0
    if remaining_seconds > SECONDS_PER_HOUR:
        floor = max(floor, 20.0)
    if _recent_foreground_activity(state, current):
        floor = max(floor, 30.0)
    if preserve_count <= 1:
        floor = max(floor, 25.0)
    return floor


def _preserve_allows_opportunistic_burn(state: AccountState, current: float, *, preserve_count: int) -> bool:
    if _remaining_pct(state, secondary=True) is None or _remaining_pct(state, secondary=False) is None:
        return False
    if state.secondary_reset_at is None or state.reset_at is None:
        return False
    weekly_floor = _weekly_pace_floor_pct(state, current)
    short_floor = _short_window_floor_pct(state, current, preserve_count=preserve_count)
    return (_remaining_pct(state, secondary=True) or 0.0) > weekly_floor and (
        _remaining_pct(state, secondary=False) or 0.0
    ) > short_floor


def _has_other_usable_foreground_capacity(
    candidate: AccountState, available: list[AccountState], current: float
) -> bool:
    preserve_count = sum((1 for state in available if _routing_policy(state) == ROUTING_POLICY_PRESERVE))
    for other in available:
        if other.account_id == candidate.account_id:
            continue
        if other.status != AccountStatus.ACTIVE:
            continue
        if _routing_policy(other) == ROUTING_POLICY_PRESERVE:
            if _preserve_allows_opportunistic_burn(other, current, preserve_count=preserve_count):
                return True
            continue
        return True
    return False


def _above_emergency_floor(state: AccountState) -> bool:
    primary_remaining = _remaining_pct(state, secondary=False)
    secondary_remaining = _remaining_pct(state, secondary=True)
    if primary_remaining is None or secondary_remaining is None:
        return False
    return (
        primary_remaining > NORMAL_LAST_ACCOUNT_EMERGENCY_FLOOR_PCT
        and secondary_remaining > NORMAL_LAST_ACCOUNT_EMERGENCY_FLOOR_PCT
    )


def _filter_opportunistic_candidates(
    available: list[AccountState], current: float
) -> tuple[list[AccountState], str | None]:
    burn_first: list[AccountState] = []
    normal: list[AccountState] = []
    preserve: list[AccountState] = []
    preserve_count = sum((1 for state in available if _routing_policy(state) == ROUTING_POLICY_PRESERVE))
    for state in available:
        policy = _routing_policy(state)
        if policy == ROUTING_POLICY_BURN_FIRST:
            if _has_other_usable_foreground_capacity(state, available, current) or _above_emergency_floor(state):
                burn_first.append(state)
        elif policy == ROUTING_POLICY_PRESERVE:
            if _preserve_allows_opportunistic_burn(state, current, preserve_count=preserve_count):
                preserve.append(state)
        elif _has_other_usable_foreground_capacity(state, available, current) or _above_emergency_floor(state):
            normal.append(state)
    if burn_first or normal or preserve:
        return ([*burn_first, *normal, *preserve], None)
    if any((_routing_policy(state) == ROUTING_POLICY_PRESERVE for state in available)):
        return ([], "preserve floor or stale usage data blocks opportunistic burn")
    return ([], "no expendable account has emergency foreground reserve")
