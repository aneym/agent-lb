from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.utils.time import utcnow
from app.modules.pools.gemini_quota import gemini_view, load_reading
from app.modules.usage.gemini_usage_repository import GeminiKeyUsage, GeminiUsageRepository
from app.modules.usage.schemas import GeminiSource, GeminiUsageResponse

SHORT_SPAN = timedelta(hours=5)
LONG_SPAN = timedelta(days=7)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _key_source(row: GeminiKeyUsage) -> GeminiSource:
    return GeminiSource(
        id=f"key:{row.api_key_id}",
        kind="key",
        name=row.key_name or "Key",
        detail=f"{row.member_name} · team key" if row.member_name else "agent-lb gateway key",
        share_percent=row.pool_share_percent,
        requests_5h=row.requests_short,
        tokens_5h=row.tokens_short,
        requests_7d=row.requests_long,
        tokens_7d=row.tokens_long,
        last_request_at=_utc(row.last_request_at),
    )


def _orphan_source(rows: list[GeminiKeyUsage]) -> GeminiSource | None:
    if not rows:
        return None
    last = max((row.last_request_at for row in rows if row.last_request_at), default=None)
    return GeminiSource(
        id="key:removed",
        kind="key",
        name="Removed or keyless",
        detail=f"{len(rows)} {'key' if len(rows) == 1 else 'keys'} no longer on file",
        requests_5h=sum(row.requests_short for row in rows),
        tokens_5h=sum(row.tokens_short for row in rows),
        requests_7d=sum(row.requests_long for row in rows),
        tokens_7d=sum(row.tokens_long for row in rows),
        last_request_at=_utc(last),
    )


def _seat_source(now: datetime) -> tuple[GeminiSource | None, str | None]:
    reading = load_reading()
    if reading is None:
        return None, None
    view = gemini_view(reading, now)
    host = reading.get("host")
    plan = reading.get("plan")
    short, week = view.counts["5h"], view.counts["7d"]
    seat = GeminiSource(
        id="neon-seat",
        kind="seat",
        name="Neon seat",
        detail=f"Antigravity CLI on {host}" if isinstance(host, str) else "Antigravity CLI",
        requests_5h=short.requests,
        tokens_5h=short.tokens,
        requests_7d=week.requests,
        tokens_7d=week.tokens,
        last_request_at=view.last_request_at,
        metered_at=view.metered_at,
    )
    return seat, plan if isinstance(plan, str) else None


async def gemini_usage(repository: GeminiUsageRepository) -> GeminiUsageResponse:
    now = utcnow()
    seat, plan = _seat_source(now.replace(tzinfo=timezone.utc))
    rows = await repository.usage_by_key(short_since=now - SHORT_SPAN, long_since=now - LONG_SPAN)
    named = sorted(
        (_key_source(row) for row in rows if row.key_name), key=lambda source: source.tokens_7d, reverse=True
    )
    orphan = _orphan_source([row for row in rows if not row.key_name])
    sources = ([seat] if seat else []) + named + ([orphan] if orphan else [])
    return GeminiUsageResponse(
        generated_at=now.replace(tzinfo=timezone.utc),
        plan=plan,
        requests_5h=sum(source.requests_5h for source in sources),
        tokens_5h=sum(source.tokens_5h for source in sources),
        requests_7d=sum(source.requests_7d for source in sources),
        tokens_7d=sum(source.tokens_7d for source in sources),
        sources=sources,
    )
