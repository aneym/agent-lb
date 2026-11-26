from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from app.core.auth.dependencies import validate_dashboard_session
from app.core.balancer.tiers import account_tier
from app.core.config.settings import get_settings
from app.core.providers import normalize_provider_name
from app.core.request_locality import is_dashboard_keyless_client, is_local_request, is_unauthenticated_client_allowed
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.schemas import AccountSummary
from app.modules.pools.service import is_pool_usable

CLIENTS = {"openai": ["codex"], "anthropic": ["claude"], "gemini": ["antigravity", "gemini"]}
_FIVE_HOURS = 300
_LIMITED = {AccountStatus.RATE_LIMITED.value, AccountStatus.QUOTA_EXCEEDED.value}


async def validate_headroom_access(request: Request) -> None:
    if is_local_request(request) or is_unauthenticated_client_allowed(request) or is_dashboard_keyless_client(request):
        return
    await validate_dashboard_session(request)


router = APIRouter(prefix="/api/pools", tags=["dashboard"], dependencies=[Depends(validate_headroom_access)])


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _remaining(summary: AccountSummary, window: str) -> float | None:
    usage = summary.usage
    value = None if usage is None else getattr(usage, f"{window}_remaining_percent")
    return None if value is None else max(0.0, min(100.0, float(value)))


def _windows(summary: AccountSummary) -> dict[str, tuple[float | None, datetime | None]]:
    windows: dict[str, tuple[float | None, datetime | None]] = {}
    for window, minutes, reset in (
        ("primary", summary.window_minutes_primary, summary.reset_at_primary),
        ("secondary", summary.window_minutes_secondary, summary.reset_at_secondary),
    ):
        if minutes is None:
            continue
        windows["five_hour" if minutes == _FIVE_HOURS else "weekly"] = (_remaining(summary, window), _utc(reset))
    return windows


def _recovery_at(summary: AccountSummary, now: datetime) -> datetime | None:
    blocking = [reset for remaining, reset in _windows(summary).values() if remaining is not None and remaining <= 0]
    if summary.status in _LIMITED and summary.rate_limit_reset_at is not None:
        blocking.append(_utc(summary.rate_limit_reset_at))
    future = [reset for reset in blocking if reset is not None and reset > now]
    if len(future) != len(blocking) or not future:
        return None
    return max(future)


def _is_usable(summary: AccountSummary) -> bool:
    if summary.status != AccountStatus.ACTIVE.value or not is_pool_usable(summary):
        return False
    return all(remaining is None or remaining > 0 for remaining, _ in _windows(summary).values())


def _share(values: list[float]) -> float | None:
    return round(sum(values) / len(values) / 100.0, 4) if values else None


def provider_headroom(summaries: list[AccountSummary], *, now: datetime, workers_per_account: int) -> dict:
    routable = [summary for summary in summaries if is_pool_usable(summary)]
    usable = [summary for summary in routable if _is_usable(summary)]
    usable_ids = {summary.account_id for summary in usable}
    five_hour: list[float] = []
    weekly: list[float] = []
    for summary in routable:
        windows = _windows(summary)
        limited = summary.account_id not in usable_ids
        if (reading := windows.get("five_hour")) and reading[0] is not None:
            five_hour.append(0.0 if limited else reading[0])
        if (reading := windows.get("weekly")) and reading[0] is not None:
            weekly.append(0.0 if limited else reading[0])
    capacity = sum(
        min([remaining for remaining, _ in _windows(summary).values() if remaining is not None] or [100.0]) / 100.0
        for summary in usable
    )
    workers = math.floor(capacity * workers_per_account)
    recoveries = [
        at for summary in routable if summary.account_id not in usable_ids and (at := _recovery_at(summary, now))
    ]
    resets_at = min(recoveries, default=None)
    return {
        "accounts": len(summaries),
        "usable": len(usable),
        "exhausted": not usable,
        "five_hour_remaining_share": _share(five_hour),
        "weekly_remaining_share": _share(weekly),
        "resets_at": resets_at.isoformat().replace("+00:00", "Z") if resets_at else None,
        "resets_in_seconds": max(0, math.ceil((resets_at - now).total_seconds())) if resets_at else None,
        "recommended_max_workers": max(workers, 1) if usable else 0,
        "personal_accounts": sum(1 for summary in summaries if account_tier(summary.account_id) == "personal"),
    }


def build_headroom(summaries: list[AccountSummary], *, now: datetime, workers_per_account: int) -> dict:
    by_provider: dict[str, list[AccountSummary]] = {}
    for summary in summaries:
        by_provider.setdefault(normalize_provider_name(summary.provider), []).append(summary)
    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "pools": {
            provider: {
                "provider": provider,
                "clients": CLIENTS.get(provider, []),
                **provider_headroom(members, now=now, workers_per_account=workers_per_account),
            }
            for provider, members in sorted(by_provider.items())
        },
    }


@router.get("/headroom")
async def pools_headroom(context: AccountsContext = Depends(get_accounts_context)) -> dict:
    now = utcnow().replace(tzinfo=timezone.utc)
    return build_headroom(
        await context.service.list_accounts(),
        now=now,
        workers_per_account=get_settings().pool_headroom_workers_per_account,
    )
