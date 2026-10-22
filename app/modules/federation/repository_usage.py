from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import to_utc_naive, utcnow
from app.db.federation_models import FederationUsageDaily
from app.db.models import Account, RequestLog
from app.modules.federation.schemas import FederationRequestLogRow, FederationUsageDayRollup
from app.modules.request_logs.edge_source import exclude_forwarded_edge_logs


@dataclass(frozen=True, slots=True)
class StoredFederationUsageRollup:
    instance_id: str
    rollup: FederationUsageDayRollup
    reported_at: datetime


class FederationUsageRepository:
    _session: AsyncSession

    async def upsert_usage_report(
        self,
        instance_id: str,
        rollups: list[FederationUsageDayRollup],
        *,
        reported_at: datetime,
    ) -> None:
        for rollup in rollups:
            key = {
                "instance_id": instance_id,
                "account_id": rollup.account_id,
                "day": rollup.day,
            }
            values = {
                "provider": rollup.provider,
                "requests": rollup.requests,
                "input_tokens": rollup.input_tokens,
                "output_tokens": rollup.output_tokens,
                "cache_read_tokens": rollup.cache_read_tokens,
                "cost": rollup.cost,
                "session_count": rollup.session_count,
                "last_request_at": to_utc_naive(rollup.last_request_at) if rollup.last_request_at else None,
                "reported_at": reported_at,
            }
            primary_key = tuple(key.values())
            existing = await self._session.get(FederationUsageDaily, primary_key)
            if existing is None:
                try:
                    async with self._session.begin_nested():
                        self._session.add(FederationUsageDaily(**key, **values))
                        await self._session.flush()
                except IntegrityError:
                    existing = await self._session.get(FederationUsageDaily, primary_key)
                    if existing is None:
                        raise
                else:
                    continue
            for field_name, value in values.items():
                setattr(existing, field_name, value)
        await self._session.commit()

    async def list_local_usage_rollups(
        self, *, window_days: int, account_ids: set[str] | None = None
    ) -> list[FederationUsageDayRollup]:
        earliest_day = (utcnow() - timedelta(days=window_days)).date()
        since = datetime.combine(earliest_day, datetime.min.time())
        day = func.date(RequestLog.requested_at)
        statement = (
            select(
                day.label("day"),
                RequestLog.account_id,
                func.max(RequestLog.provider).label("provider"),
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cache_read_tokens), 0).label("cache_read_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost"),
                func.count(func.distinct(RequestLog.session_id)).label("session_count"),
                func.max(RequestLog.requested_at).label("last_request_at"),
            )
            .where(
                and_(
                    RequestLog.account_id.is_not(None),
                    RequestLog.deleted_at.is_(None),
                    RequestLog.requested_at >= since,
                    exclude_forwarded_edge_logs(),
                )
            )
            .group_by(day, RequestLog.account_id)
            .order_by(day.desc(), RequestLog.account_id.asc())
        )
        if account_ids is not None:
            statement = statement.where(RequestLog.account_id.in_(account_ids))
        rows = (await self._session.execute(statement)).all()
        return [
            FederationUsageDayRollup(
                day=date.fromisoformat(str(row.day)),
                account_id=str(row.account_id),
                provider=str(row.provider),
                requests=int(row.requests),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cache_read_tokens=int(row.cache_read_tokens),
                cost=float(row.cost),
                session_count=int(row.session_count),
                last_request_at=row.last_request_at,
            )
            for row in rows
        ]

    async def list_request_logs_to_forward(
        self,
        *,
        after_id: int | None,
        since: datetime | None,
        limit: int,
    ) -> list[RequestLog]:
        statement = select(RequestLog).where(RequestLog.deleted_at.is_(None))
        if after_id is not None:
            statement = statement.where(RequestLog.id > after_id)
        elif since is not None:
            statement = statement.where(RequestLog.requested_at >= since)
        statement = statement.order_by(RequestLog.id.asc()).limit(limit)
        return list((await self._session.execute(statement)).scalars().all())

    async def max_request_log_id(self) -> int | None:
        value = (await self._session.execute(select(func.max(RequestLog.id)))).scalar()
        return None if value is None else int(value)

    async def ingest_forwarded_request_logs(
        self,
        instance_id: str,
        rows: list[FederationRequestLogRow],
    ) -> tuple[int, int, int]:
        source = f"edge:{instance_id}"
        max_source_row_id = max((row.source_row_id for row in rows), default=0)
        if not rows:
            return 0, 0, max_source_row_id
        request_ids = [row.request_id for row in rows]
        existing = await self._session.execute(
            select(RequestLog.request_id).where(
                RequestLog.source == source,
                RequestLog.request_id.in_(request_ids),
            )
        )
        seen = set(existing.scalars().all())
        account_ids = {row.account_id for row in rows if row.account_id}
        known_accounts: set[str] = set()
        if account_ids:
            found = await self._session.execute(select(Account.id).where(Account.id.in_(account_ids)))
            known_accounts = set(found.scalars().all())
        accepted = 0
        skipped = 0
        for row in rows:
            if row.request_id in seen:
                skipped += 1
                continue
            seen.add(row.request_id)
            account_id = row.account_id if row.account_id in known_accounts else None
            self._session.add(
                RequestLog(
                    account_id=account_id,
                    provider=row.provider,
                    api_key_id=None,
                    session_id=row.session_id,
                    client_session_id=row.client_session_id,
                    caller_user=row.caller_user,
                    caller_user_source=row.caller_user_source,
                    caller_machine=row.caller_machine,
                    caller_machine_source=row.caller_machine_source,
                    caller_seat=row.caller_seat,
                    room=row.room,
                    unified_5h_utilization=row.unified_5h_utilization,
                    unified_7d_utilization=row.unified_7d_utilization,
                    request_id=row.request_id,
                    request_kind=row.request_kind,
                    requested_at=to_utc_naive(row.requested_at),
                    model=row.model,
                    plan_type=row.plan_type,
                    source=source,
                    useragent=row.useragent,
                    useragent_group=row.useragent_group,
                    transport=row.transport,
                    service_tier=row.service_tier,
                    requested_service_tier=row.requested_service_tier,
                    actual_service_tier=row.actual_service_tier,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    cached_input_tokens=row.cached_input_tokens,
                    cache_creation_tokens=row.cache_creation_tokens,
                    cache_read_tokens=row.cache_read_tokens,
                    reasoning_tokens=row.reasoning_tokens,
                    cost_usd=row.cost_usd,
                    reasoning_effort=row.reasoning_effort,
                    latency_ms=row.latency_ms,
                    latency_first_token_ms=row.latency_first_token_ms,
                    status=row.status,
                    error_code=row.error_code,
                    error_message=row.error_message,
                    failure_phase=row.failure_phase,
                    failure_detail=row.failure_detail,
                    failure_exception_type=row.failure_exception_type,
                    upstream_status_code=row.upstream_status_code,
                    upstream_error_code=row.upstream_error_code,
                    bridge_stage=row.bridge_stage,
                    upstream_proxy_route_mode=row.upstream_proxy_route_mode,
                    upstream_proxy_pool_id=row.upstream_proxy_pool_id,
                    upstream_proxy_endpoint_id=row.upstream_proxy_endpoint_id,
                    upstream_proxy_fallback_used=row.upstream_proxy_fallback_used,
                    upstream_proxy_fail_closed_reason=row.upstream_proxy_fail_closed_reason,
                )
            )
            accepted += 1
        await self._session.commit()
        return accepted, skipped, max_source_row_id

    async def list_stored_usage_rollups(self, *, window_days: int) -> list[StoredFederationUsageRollup]:
        earliest_day = (utcnow() - timedelta(days=window_days)).date()
        rows = (
            (await self._session.execute(select(FederationUsageDaily).where(FederationUsageDaily.day >= earliest_day)))
            .scalars()
            .all()
        )
        return [
            StoredFederationUsageRollup(
                instance_id=row.instance_id,
                rollup=FederationUsageDayRollup(
                    day=row.day,
                    account_id=row.account_id,
                    provider=row.provider,
                    requests=row.requests,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    cache_read_tokens=row.cache_read_tokens,
                    cost=row.cost,
                    session_count=row.session_count,
                    last_request_at=row.last_request_at,
                ),
                reported_at=row.reported_at,
            )
            for row in rows
        ]
