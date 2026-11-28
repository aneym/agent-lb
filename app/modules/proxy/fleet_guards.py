from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.balancer.state import AccountState, ExcludedAccount, SelectionResult
from app.core.config.settings import Settings
from app.db.access_models import TeamMember, TeamMemberStatus
from app.modules.proxy.account_lease_policy import (
    account_cap_error_code,
    filter_states_for_account_caps,
    record_account_cap_rejection,
)
from app.modules.proxy.runtime_state import AccountLeaseKind, RuntimeState

_FIVE_HOUR_WINDOW_SECONDS = 5 * 3600.0


@dataclass(frozen=True, slots=True)
class TeamHeadroomFloors:
    default_floor: float = 0.0
    by_account_id: dict[str, float] = field(default_factory=dict)


def _parse_json_value(raw: object) -> object:
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _member_share_for_provider(member: TeamMember, provider_name: str) -> float | None:
    by_provider = _parse_json_value(member.pool_share_percent_by_provider)
    if isinstance(by_provider, dict) and provider_name in by_provider:
        value = by_provider[provider_name]
        if isinstance(value, (int, float)):
            return float(value)
        return None
    if provider_name == "openai" and member.pool_share_percent is not None:
        return float(member.pool_share_percent)
    return None


async def load_team_headroom_floors(
    session: AsyncSession | None,
    *,
    provider_name: str,
    settings: Settings,
) -> TeamHeadroomFloors:
    if session is None or not settings.proxy_unauthenticated_respect_team_pool_share:
        return TeamHeadroomFloors()
    result = await session.execute(select(TeamMember).where(TeamMember.status == TeamMemberStatus.ACTIVE))
    members = result.scalars().all()
    default_floor = 0.0
    by_account_id: dict[str, float] = {}
    pool_cap = float(settings.proxy_unauthenticated_team_share_pool_cap_percent)
    account_cap = float(settings.proxy_unauthenticated_team_share_account_cap_percent)
    for member in members:
        share = _member_share_for_provider(member, provider_name)
        if share is None or share <= 0.0 or share >= 100.0:
            continue
        raw_ids = _parse_json_value(member.pool_share_account_ids)
        scoped_ids = [str(item).strip() for item in raw_ids if str(item).strip()] if isinstance(raw_ids, list) else []
        if scoped_ids:
            for account_id in scoped_ids:
                by_account_id[account_id] = min(account_cap, by_account_id.get(account_id, 0.0) + share)
        else:
            default_floor = min(pool_cap, default_floor + share)
    return TeamHeadroomFloors(default_floor=default_floor, by_account_id=by_account_id)


def effective_unauthenticated_headroom_floor(
    state: AccountState,
    floors: TeamHeadroomFloors,
    settings: Settings,
) -> float:
    plan = (state.plan_type or "").strip().lower()
    base_floor = (
        float(settings.proxy_unauthenticated_plus_headroom_floor_percent)
        if plan == "plus"
        else float(settings.proxy_unauthenticated_headroom_floor_percent)
    )
    account_floor = floors.by_account_id.get(state.account_id, 0.0)
    if account_floor == 0.0 and floors.by_account_id:
        for prefix, value in floors.by_account_id.items():
            if state.account_id.startswith(prefix):
                account_floor = max(account_floor, value)
    return min(95.0, max(base_floor, floors.default_floor, account_floor))


def filter_unauthenticated_headroom(
    states: Iterable[AccountState],
    *,
    floors: TeamHeadroomFloors,
    settings: Settings,
) -> tuple[list[AccountState], list[AccountState]]:
    kept: list[AccountState] = []
    reserved: list[AccountState] = []
    for state in states:
        floor = effective_unauthenticated_headroom_floor(state, floors, settings)
        if floor <= 0.0:
            kept.append(state)
            continue
        limit_used = 100.0 - floor
        primary_used = state.raw_used_percent if state.raw_used_percent is not None else state.used_percent
        secondary_used = (
            state.raw_secondary_used_percent
            if state.raw_secondary_used_percent is not None
            else state.secondary_used_percent
        )
        primary_blocked = primary_used is not None and primary_used >= limit_used
        secondary_blocked = secondary_used is not None and secondary_used >= limit_used
        if primary_blocked or secondary_blocked:
            reserved.append(state)
        else:
            kept.append(state)
    return kept, reserved


def _ts_utc(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def headroom_reserved_selection_result(
    reserved_states: Iterable[AccountState],
    *,
    floors: TeamHeadroomFloors,
    settings: Settings,
    current: float,
) -> SelectionResult:
    state_list = list(reserved_states)
    excluded_accounts: list[ExcludedAccount] = []
    recovery_candidates: list[float] = []
    for state in state_list:
        floor = effective_unauthenticated_headroom_floor(state, floors, settings)
        limit_used = 100.0 - floor
        primary_used = state.raw_used_percent if state.raw_used_percent is not None else state.used_percent
        secondary_used = (
            state.raw_secondary_used_percent
            if state.raw_secondary_used_percent is not None
            else state.secondary_used_percent
        )
        blockers: list[float] = []
        if (
            primary_used is not None
            and primary_used >= limit_used
            and state.primary_reset_at is not None
            and state.primary_reset_at > current
        ):
            blockers.append(float(state.primary_reset_at))
        if (
            secondary_used is not None
            and secondary_used >= limit_used
            and state.secondary_reset_at is not None
            and state.secondary_reset_at > current
        ):
            blockers.append(float(state.secondary_reset_at))
        if state.reset_at is not None and state.reset_at > current:
            blockers.append(float(state.reset_at))
        account_recovery = max(blockers) if blockers else None
        if account_recovery is not None:
            recovery_candidates.append(account_recovery)
        excluded_accounts.append(
            ExcludedAccount(
                account_id=state.account_id,
                status=state.status.value,
                reset_at=_ts_utc(account_recovery),
                cooldown_until=_ts_utc(state.cooldown_until),
                deactivation_reason="human_headroom_reserved",
                plan_type=state.plan_type,
                transient_backoff_until=None,
            )
        )
    retry_at = _ts_utc(min(recovery_candidates)) if recovery_candidates else None
    return SelectionResult(
        account=None,
        error_message="No available accounts (human headroom reserved)",
        excluded_accounts=excluded_accounts,
        retry_at=retry_at,
    )


def unauthenticated_concurrency_cap(
    state: AccountState,
    *,
    now: float | None = None,
    settings: Settings,
) -> int:
    plan = (state.plan_type or "").strip().lower()
    base_cap = (
        int(settings.proxy_unauthenticated_plus_concurrency_limit)
        if plan == "plus"
        else int(settings.proxy_unauthenticated_account_concurrency_limit)
    )
    if base_cap <= 0:
        return 0
    post_reset_cap = int(settings.proxy_unauthenticated_post_reset_concurrency_limit)
    post_reset_window = int(settings.proxy_unauthenticated_post_reset_window_seconds)
    if post_reset_cap > 0 and post_reset_window > 0:
        in_post_reset_window = False
        if state.primary_reset_at is not None:
            current = time.time() if now is None else now
            window_start = float(state.primary_reset_at) - _FIVE_HOUR_WINDOW_SECONDS
            elapsed = current - window_start
            in_post_reset_window = 0.0 <= elapsed < float(post_reset_window)
        elif state.raw_used_percent is not None and state.raw_used_percent <= 1.0:
            in_post_reset_window = True
        if in_post_reset_window:
            effective_post_reset = min(post_reset_cap, 1) if plan == "plus" else post_reset_cap
            return min(base_cap, effective_post_reset)
    return base_cap


def filter_states_for_unauthenticated_concurrency(
    states: Iterable[AccountState],
    *,
    lease_kind: AccountLeaseKind | None,
    settings: Settings,
    now: float | None = None,
) -> list[AccountState]:
    if lease_kind is None:
        return list(states)
    current = time.time() if now is None else now
    filtered: list[AccountState] = []
    for state in states:
        cap = unauthenticated_concurrency_cap(state, now=current, settings=settings)
        if cap > 0 and max(state.inflight_response_creates, state.inflight_streams) >= cap:
            continue
        filtered.append(state)
    return filtered


def unauthenticated_lease_allowed(
    runtime: RuntimeState,
    state: AccountState | None,
    *,
    settings: Settings,
    now: float | None = None,
) -> bool:
    if state is None:
        return True
    cap = unauthenticated_concurrency_cap(state, now=now, settings=settings)
    if cap <= 0:
        return True
    return max(runtime.inflight_response_creates, runtime.inflight_streams) < cap


def filter_candidates_for_caller(
    states: list[AccountState],
    *,
    is_unauthenticated: bool,
    hard_sticky: bool = False,
    lease_kind: AccountLeaseKind | None,
    floors: TeamHeadroomFloors,
    settings: Settings,
) -> tuple[list[AccountState], SelectionResult | None, str | None]:
    headroom_states = states
    if is_unauthenticated:
        headroom_states, reserved_states = filter_unauthenticated_headroom(
            states,
            floors=floors,
            settings=settings,
        )
        if not headroom_states and states:
            return (
                [],
                headroom_reserved_selection_result(
                    reserved_states,
                    floors=floors,
                    settings=settings,
                    current=time.time(),
                ),
                "no_accounts",
            )
    if hard_sticky:
        return headroom_states, None, None
    cap_candidates = (
        filter_states_for_unauthenticated_concurrency(
            headroom_states,
            lease_kind=lease_kind,
            settings=settings,
        )
        if is_unauthenticated
        else headroom_states
    )
    selection_states = filter_states_for_account_caps(cap_candidates, lease_kind=lease_kind)
    if not selection_states and headroom_states:
        record_account_cap_rejection(lease_kind)
        return [], SelectionResult(None, "No available accounts"), account_cap_error_code(lease_kind)
    return selection_states, None, None
