from __future__ import annotations

from app.core.usage.types import UsageWindowRow
from app.db.models import AdditionalUsageHistory, UsageHistory


def usage_history_to_window_row(entry: UsageHistory | AdditionalUsageHistory) -> UsageWindowRow:

    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )
