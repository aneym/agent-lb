from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
FIRST_WEEK = date(2026, 11, 9)
FIRST_WEEK_CAP = 2
WEEK_CAP = 1
FULL_PERCENT = 95.0


def week_start(now: datetime) -> datetime:
    local = now.astimezone(EASTERN)
    monday = (local - timedelta(days=local.weekday())).date()
    return datetime(monday.year, monday.month, monday.day, tzinfo=EASTERN).astimezone(timezone.utc)


def week_cap(start: datetime) -> int:
    return FIRST_WEEK_CAP if start.astimezone(EASTERN).date() == FIRST_WEEK else WEEK_CAP
