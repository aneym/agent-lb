from sqlalchemy import literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prompt_tokens import SESSION_KEY_SQL
from app.db.models import RequestLog
from app.db.models_trace import RequestContextParts
from app.modules.sessions.repository import SessionsRepository


class SessionTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rows(self, session_id: str, limit: int) -> list[tuple[RequestLog, RequestContextParts | None]]:
        result = await self._session.execute(
            select(RequestLog, RequestContextParts)
            .outerjoin(RequestContextParts, RequestContextParts.request_log_id == RequestLog.id)
            .where(SessionsRepository._eligible_clause(), literal_column(SESSION_KEY_SQL) == session_id)
            .order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
            .limit(limit)
        )
        return [(log, parts) for log, parts in result.all()]
