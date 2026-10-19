from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class WorkingCalendar(Protocol):
    timezone: str
    working_days: tuple[int, ...]
    working_hours_start: str
    working_hours_end: str
    prewarm_lead_minutes: int


def parse_working_days(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return (0, 1, 2, 3, 4)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return (0, 1, 2, 3, 4)
    days: list[int] = []
    if isinstance(decoded, list):
        for value in decoded:
            if isinstance(value, int) and 0 <= value <= 6 and value not in days:
                days.append(value)
    return tuple(days) or (0, 1, 2, 3, 4)


def encode_working_days(days: tuple[int, ...] | list[int]) -> str:
    normalized = sorted({int(day) for day in days if 0 <= int(day) <= 6})
    return json.dumps(normalized or [0, 1, 2, 3, 4], separators=(",", ":"))


def _inside_work_block(value: datetime, settings: WorkingCalendar) -> bool:
    local = _to_planner_tz(value, settings.timezone)
    if local.weekday() not in settings.working_days:
        return False
    start = _parse_hhmm(settings.working_hours_start, dt_time(9, 0))
    end = _parse_hhmm(settings.working_hours_end, dt_time(18, 0))
    current_time = local.time()
    if start <= end:
        return start <= current_time < end
    return current_time >= start or current_time < end


def _inside_prewarm_band(value: datetime, settings: WorkingCalendar) -> bool:
    if _inside_work_block(value, settings):
        return True
    lead = timedelta(minutes=max(0, settings.prewarm_lead_minutes))
    return _inside_work_block(value + lead, settings)


def _to_planner_tz(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    return value.astimezone(tz)


def _parse_hhmm(raw: str, fallback: dt_time) -> dt_time:
    try:
        hours, minutes = raw.split(":", 1)
        return dt_time(int(hours), int(minutes))
    except (TypeError, ValueError):
        return fallback


def _floor_datetime(value: datetime, slot_seconds: int) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % slot_seconds), tz=timezone.utc)


def _next_work_starts(
    now: datetime,
    settings: WorkingCalendar,
    *,
    count: int,
    offsets: tuple[int, ...],
) -> list[datetime]:
    local_now = _to_planner_tz(now, settings.timezone)
    start = _parse_hhmm(settings.working_hours_start, dt_time(9, 0))
    results: list[datetime] = []
    for day_offset in range(0, 14):
        candidate_day = local_now.date() + timedelta(days=day_offset)
        local_start = datetime.combine(candidate_day, start, tzinfo=local_now.tzinfo)
        if local_start.weekday() not in settings.working_days or local_start < local_now:
            continue
        for hours in offsets:
            results.append((local_start + timedelta(hours=hours)).astimezone(timezone.utc))
        if len(results) >= count * len(offsets):
            break
    return results


def _warmup_cycle_key(scheduled_at: datetime, settings: WorkingCalendar) -> str:
    local = _to_planner_tz(scheduled_at, settings.timezone)
    lead = max(1, settings.prewarm_lead_minutes)
    cycle_minute = (local.hour * 60 + local.minute) // lead
    return f"{local:%Y%m%d}:warmup_cycle:{cycle_minute}"


def _calendar_prior_units(value: datetime, settings: WorkingCalendar) -> float:
    if _inside_work_block(value, settings):
        return 6.0
    if _inside_prewarm_band(value, settings):
        return 2.0
    return 0.2


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "none"
    return value.astimezone(timezone.utc).isoformat()
