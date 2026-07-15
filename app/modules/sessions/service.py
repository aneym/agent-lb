from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.core.utils.time import utcnow
from app.modules.request_logs.mappers import to_request_log_entry
from app.modules.sessions.mappers import to_model_breakdowns, to_session_aggregates
from app.modules.sessions.repository import SessionsRepository
from app.modules.sessions.schemas import SessionAggregate, SessionDetailResponse


@dataclass(frozen=True, slots=True)
class SessionListPage:
    sessions: list[SessionAggregate]
    total: int


class SessionsService:
    def __init__(self, repository: SessionsRepository) -> None:
        self._repository = repository

    async def list_sessions(
        self,
        *,
        window_minutes: int,
        limit: int,
        offset: int,
    ) -> SessionListPage:
        since = utcnow() - timedelta(minutes=window_minutes)
        rows, total = await self._repository.list_aggregates(
            since=since,
            limit=limit,
            offset=offset,
        )
        model_rows = await self._repository.list_models(
            [row.session_id for row in rows],
            since=since,
        )
        return SessionListPage(
            sessions=to_session_aggregates(rows, model_rows),
            total=total,
        )

    async def get_session(self, session_id: str) -> SessionDetailResponse | None:
        row = await self._repository.get_aggregate(session_id)
        if row is None:
            return None
        model_rows = await self._repository.list_models([session_id])
        recent_rows = await self._repository.list_recent_requests(session_id, limit=50)
        aggregate = to_session_aggregates([row], model_rows)[0]
        return SessionDetailResponse(
            session=aggregate,
            by_model=to_model_breakdowns(model_rows),
            recent_requests=[to_request_log_entry(request) for request in recent_rows],
        )

    async def resolve_session_ids(self, value: str) -> list[str]:
        return await self._repository.resolve_session_id(value)
