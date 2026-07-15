from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from app.core.utils.time import utcnow
from app.modules.request_logs.mappers import to_request_log_entry
from app.modules.sessions.mappers import to_model_breakdowns, to_session_aggregates
from app.modules.sessions.repository import SessionHistogramRow, SessionsRepository
from app.modules.sessions.schemas import (
    SessionAggregate,
    SessionAnalyticsResponse,
    SessionDetailResponse,
    SessionHistogramBin,
    SessionSeat,
    SessionSeriesBucket,
    SessionSeriesModel,
)


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
        session_ids = [row.session_id for row in rows]
        model_rows = await self._repository.list_models(session_ids, since=since)
        sparkline_rows = await self._repository.list_sparklines(
            session_ids,
            since=since,
            until=utcnow(),
        )
        sparklines = {session_id: [0] * 24 for session_id in session_ids}
        for sparkline_row in sparkline_rows:
            if 0 <= sparkline_row.bucket_index < 24:
                sparklines[sparkline_row.session_id][sparkline_row.bucket_index] = (
                    sparkline_row.requests
                )
        return SessionListPage(
            sessions=to_session_aggregates(rows, model_rows, sparklines),
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

    async def get_analytics(
        self,
        session_id: str,
        *,
        window_minutes: int,
    ) -> SessionAnalyticsResponse | None:
        until = utcnow()
        since = until - timedelta(minutes=window_minutes)
        row = await self._repository.get_aggregate(
            session_id,
            since=since,
            until=until,
        )
        if row is None:
            return None
        bucket_seconds = max(60, ceil(window_minutes * 60 / 48 / 60) * 60)
        series_rows = await self._repository.list_series(
            session_id,
            since=since,
            until=until,
            bucket_seconds=bucket_seconds,
        )
        seat_rows = await self._repository.list_seats(session_id, since=since, until=until)
        latency_rows = await self._repository.list_latency_histogram(
            session_id,
            since=since,
            until=until,
        )
        token_rows = await self._repository.list_tokens_per_request_histogram(
            session_id,
            since=since,
            until=until,
        )
        models_by_bucket: dict[datetime, list[SessionSeriesModel]] = defaultdict(list)
        for series_row in series_rows:
            models_by_bucket[series_row.bucket_start].append(
                SessionSeriesModel(
                    model=series_row.model,
                    reasoning_effort=series_row.reasoning_effort,
                    requests=series_row.requests,
                    output_tokens=series_row.output_tokens,
                    cached_input_tokens=series_row.cached_input_tokens,
                    cost_usd=series_row.cost_usd,
                )
            )
        aggregate = to_session_aggregates(
            [row],
            await self._repository.list_models([session_id], since=since, until=until),
        )[0]
        return SessionAnalyticsResponse(
            session=aggregate,
            bucket_seconds=bucket_seconds,
            series=[
                SessionSeriesBucket(bucket_start=bucket_start, by_model=models)
                for bucket_start, models in models_by_bucket.items()
            ],
            seats=[
                SessionSeat(
                    model=seat.model,
                    reasoning_effort=seat.reasoning_effort,
                    requests=seat.requests,
                    input_tokens=seat.input_tokens,
                    output_tokens=seat.output_tokens,
                    cached_input_tokens=seat.cached_input_tokens,
                    cost_usd=seat.cost_usd,
                    errors=seat.errors,
                )
                for seat in seat_rows
            ],
            latency_histogram=self._fill_histogram(
                latency_rows,
                ["0-1s", "1-2s", "2-5s", "5-10s", "10-30s", "30-60s", ">60s"],
            ),
            tokens_per_request_histogram=self._fill_histogram(
                token_rows,
                ["<100", "100-500", "500-2K", "2K-10K", "10K-50K", ">50K"],
            ),
        )

    @staticmethod
    def _fill_histogram(
        rows: list[SessionHistogramRow],
        labels: list[str],
    ) -> list[SessionHistogramBin]:
        counts = {row.label: row.count for row in rows}
        return [SessionHistogramBin(label=label, count=counts.get(label, 0)) for label in labels]

    async def resolve_session_ids(self, value: str) -> list[str]:
        return await self._repository.resolve_session_id(value)
