from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers.gemini import GEMINI_PROVIDER_NAME
from app.db.access_models import TeamMember
from app.db.request_models import ApiKey, RequestLog


@dataclass(frozen=True, slots=True)
class GeminiKeyUsage:
    api_key_id: str | None
    key_name: str | None
    member_name: str | None
    pool_share_percent: float | None
    requests_short: int
    tokens_short: int
    requests_long: int
    tokens_long: int
    last_request_at: datetime | None


class GeminiUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def usage_by_key(self, *, short_since: datetime, long_since: datetime) -> list[GeminiKeyUsage]:
        tokens = (
            func.coalesce(RequestLog.input_tokens, 0)
            + func.coalesce(RequestLog.output_tokens, 0)
            + func.coalesce(RequestLog.cached_input_tokens, 0)
        )
        recent = RequestLog.requested_at >= short_since
        totals = (
            await self._session.execute(
                select(
                    RequestLog.api_key_id,
                    func.sum(case((recent, 1), else_=0)),
                    func.sum(case((recent, tokens), else_=0)),
                    func.count(),
                    func.sum(tokens),
                    func.max(RequestLog.requested_at),
                )
                .where(
                    RequestLog.provider == GEMINI_PROVIDER_NAME,
                    RequestLog.requested_at >= long_since,
                    RequestLog.deleted_at.is_(None),
                )
                .group_by(RequestLog.api_key_id)
            )
        ).all()
        key_ids = [row[0] for row in totals if row[0]]
        owners = {
            key_id: (key_name, member, shares)
            for key_id, key_name, member, shares in (
                await self._session.execute(
                    select(ApiKey.id, ApiKey.name, TeamMember.name, TeamMember.pool_share_percent_by_provider)
                    .outerjoin(TeamMember, TeamMember.id == ApiKey.member_id)
                    .where(ApiKey.id.in_(key_ids))
                )
            ).all()
        }
        usage: list[GeminiKeyUsage] = []
        for key_id, short_count, short_tokens, count, total, last in totals:
            key_name, member, shares = owners.get(key_id, (None, None, None))
            share = shares.get(GEMINI_PROVIDER_NAME) if isinstance(shares, dict) else None
            usage.append(
                GeminiKeyUsage(
                    api_key_id=key_id,
                    key_name=key_name,
                    member_name=member,
                    pool_share_percent=float(share) if isinstance(share, (int, float)) else None,
                    requests_short=int(short_count or 0),
                    tokens_short=int(short_tokens or 0),
                    requests_long=int(count or 0),
                    tokens_long=int(total or 0),
                    last_request_at=last,
                )
            )
        return usage
