from __future__ import annotations

from collections import defaultdict

from app.modules.sessions.repository import SessionAggregateRow, SessionModelRow
from app.modules.sessions.schemas import SessionAggregate, SessionModelBreakdown, SessionModelRequests


def to_session_aggregates(
    rows: list[SessionAggregateRow],
    model_rows: list[SessionModelRow],
) -> list[SessionAggregate]:
    models_by_session: dict[str, list[SessionModelRequests]] = defaultdict(list)
    for model_row in model_rows:
        models_by_session[model_row.session_id].append(
            SessionModelRequests(model=model_row.model, requests=model_row.requests)
        )
    return [
        SessionAggregate(
            session_id=row.session_id,
            provider=row.provider,
            useragent_group=row.useragent_group,
            models=models_by_session[row.session_id],
            requests=row.requests,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cached_input_tokens=row.cached_input_tokens,
            cost_usd=row.cost_usd,
            errors=row.errors,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
        )
        for row in rows
    ]


def to_model_breakdowns(rows: list[SessionModelRow]) -> list[SessionModelBreakdown]:
    return [
        SessionModelBreakdown(
            model=row.model,
            requests=row.requests,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cached_input_tokens=row.cached_input_tokens,
            cost_usd=row.cost_usd,
        )
        for row in rows
    ]
