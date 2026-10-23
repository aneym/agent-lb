from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select

from app.db.models import RequestLog
from app.modules.request_logs.edge_source import exclude_forwarded_edge_logs

_EXCLUDED_REQUEST_KINDS = ("warmup", "limit_warmup")


@dataclass(frozen=True, slots=True)
class UsageRollupSums:
    request_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    total_cost_usd: float
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


def deduped_usage_aggregate_stmt(
    *,
    account_ids: list[str] | None = None,
    after_exclusive: datetime | None = None,
    until_inclusive: datetime | None = None,
) -> Select[tuple[str | None, int, int | None, int, int | None, int | None, int | None, float | None]]:
    """Per-account usage aggregate collapsing duplicate rows to the latest id.

    Duplicate request-log rows share the exact `(account_id, request_id,
    requested_at)` key (see #904); only the `max(id)` row of each group
    contributes. Warmup kinds and soft-deleted rows are excluded. Bounds are
    `requested_at`-based, so a duplicate group can never straddle them.
    """
    output_tokens_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
    conditions: list = [
        RequestLog.request_kind.not_in(_EXCLUDED_REQUEST_KINDS),
        exclude_forwarded_edge_logs(),
        RequestLog.deleted_at.is_(None),
        RequestLog.account_id.is_not(None),
    ]
    if account_ids:
        conditions.append(RequestLog.account_id.in_(account_ids))
    if after_exclusive is not None:
        conditions.append(RequestLog.requested_at > after_exclusive)
    if until_inclusive is not None:
        conditions.append(RequestLog.requested_at <= until_inclusive)

    latest_request_log_ids = (
        select(func.max(RequestLog.id).label("request_log_id"))
        .where(*conditions)
        .group_by(
            RequestLog.account_id,
            RequestLog.request_id,
            RequestLog.requested_at,
        )
        .subquery("latest_request_log_ids")
    )
    return (
        select(
            RequestLog.account_id,
            func.count(RequestLog.id).label("request_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cache_creation_tokens), 0).label("cache_creation_tokens"),
            func.coalesce(func.sum(RequestLog.cache_read_tokens), 0).label("cache_read_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
        )
        .join(latest_request_log_ids, RequestLog.id == latest_request_log_ids.c.request_log_id)
        .group_by(RequestLog.account_id)
    )


def api_key_usage_aggregate_stmt(
    *,
    api_key_ids: list[str] | None = None,
    after_exclusive: datetime | None = None,
    until_inclusive: datetime | None = None,
) -> Select[tuple[str | None, int, int | None, int, int | None, float | None]]:
    """Per-API-key usage aggregate matching the API-key summary semantics:
    no duplicate collapsing, soft-deleted rows included, warmup kinds
    excluded. Bounds are `requested_at`-based, governed by the same fold
    watermark as the account rollup."""
    output_tokens_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
    conditions: list = [
        RequestLog.api_key_id.is_not(None),
        RequestLog.request_kind.not_in(_EXCLUDED_REQUEST_KINDS),
        exclude_forwarded_edge_logs(),
    ]
    if api_key_ids:
        conditions.append(RequestLog.api_key_id.in_(api_key_ids))
    if after_exclusive is not None:
        conditions.append(RequestLog.requested_at > after_exclusive)
    if until_inclusive is not None:
        conditions.append(RequestLog.requested_at <= until_inclusive)
    return (
        select(
            RequestLog.api_key_id,
            func.count(RequestLog.id).label("request_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
        )
        .where(*conditions)
        .group_by(RequestLog.api_key_id)
    )
