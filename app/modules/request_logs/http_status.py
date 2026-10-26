from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.request_models import RequestKind, RequestLog


async def http_status_counts_since(
    session: AsyncSession, since: datetime, until: datetime
) -> dict[str, dict[str, int]]:
    status_code = RequestLog.upstream_status_code
    result = await session.execute(
        select(
            RequestLog.provider,
            func.count(),
            func.coalesce(func.sum(case((status_code.between(400, 499), 1), else_=0)), 0),
            func.coalesce(func.sum(case((status_code >= 500, 1), else_=0)), 0),
            func.coalesce(func.sum(case(((RequestLog.status != "success") & status_code.is_(None), 1), else_=0)), 0),
        )
        .where(
            RequestLog.requested_at >= since,
            RequestLog.requested_at <= until,
            RequestLog.request_kind.not_in((RequestKind.WARMUP.value, "limit_warmup")),
        )
        .group_by(RequestLog.provider)
    )
    return {
        str(provider or "unknown"): {
            "requests": int(total),
            "http_4xx": int(client_errors),
            "http_5xx": int(server_errors),
            "failed_without_status": int(unstatused),
        }
        for provider, total, client_errors, server_errors, unstatused in result.all()
    }
