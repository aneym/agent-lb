from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules.pools.schemas import PoolWindow
from app.modules.pools.seat_schemas import SeatAccount, SeatQuotaWindow


def parse_seat_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def pool_quota_source(accounts: list[SeatAccount]) -> str:
    sources = {account.quota.source for account in accounts if account.quota.windows}
    return "mixed" if len(sources) > 1 else next(iter(sources), "unavailable")


def pool_quota_windows(accounts: list[SeatAccount], window_id: str | None = None) -> list[PoolWindow]:
    grouped: dict[str, list[SeatQuotaWindow]] = {}
    seen: dict[tuple[str, str], SeatQuotaWindow] = {}
    for account in accounts:
        for window in account.quota.windows:
            key = (account.plan or account.id, window.id)
            if window_id is not None and window.id != window_id:
                continue
            previous = seen.get(key)
            priority = {"estimate": 0, "vendor_observed": 1, "vendor": 2}
            if previous is None or priority.get(window.source, 0) > priority.get(previous.source, 0):
                seen[key] = window
    for (_, key), window in seen.items():
        grouped.setdefault(key, []).append(window)
    result = []
    for key, windows in grouped.items():
        measured = [window for window in windows if window.used_percent is not None]
        if not measured:
            continue
        resets = [window.resets_at for window in measured if window.resets_at]
        result.append(
            PoolWindow(
                id=key,
                kind=measured[0].kind,
                label=measured[0].label,
                used_percent=sum(window.used_percent for window in measured) / len(measured),
                remaining_percent=sum(window.remaining_percent or 0 for window in measured) / len(measured),
                resets_at=min(resets) if resets else None,
                measured_accounts=sum(
                    any(w.id == key and w.used_percent is not None for w in a.quota.windows) for a in accounts
                ),
                source=(measured[0].source if len({window.source for window in measured}) == 1 else "mixed"),
            )
        )
    return result
