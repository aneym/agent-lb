from __future__ import annotations

from datetime import datetime

from app.db.models import AdditionalUsageHistory, UsageHistory


def extract_credit_status(
    *entries: UsageHistory | AdditionalUsageHistory | None,
) -> tuple[bool | None, bool | None, float | None]:
    credit_entries: list[UsageHistory] = [
        entry
        for entry in entries
        if isinstance(entry, UsageHistory)
        and not (entry.credits_has is None and entry.credits_unlimited is None and entry.credits_balance is None)
    ]
    if not credit_entries:
        return None, None, None
    entry = max(
        credit_entries,
        key=lambda item: item.recorded_at if item.recorded_at is not None else datetime.min,
    )
    if entry is not None:
        return entry.credits_has, entry.credits_unlimited, entry.credits_balance
    return None, None, None
