from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, cast, func, or_, select, update

from app.db.access_models import ApiKeyUsageReservation, ApiKeyUsageReservationItem
from app.db.models import RequestLog
from app.db.request_models import LimitType
from app.db.session import sqlite_writer_section
from app.modules.api_keys.repository_data import _STALE_USAGE_RESERVATION_RELEASE_BATCH_SIZE, UsageReservationItemData


class ApiKeyReservationCleanup:
    async def get_limit_usage_value(
        self, key_id: str, *, limit_type: LimitType, since: datetime, until: datetime, model_filter: str | None
    ) -> int:
        if limit_type == LimitType.CREDITS:
            return 0
        if limit_type == LimitType.TOTAL_TOKENS:
            value_expr = func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(
                RequestLog.output_tokens, RequestLog.reasoning_tokens, 0
            )
        elif limit_type == LimitType.INPUT_TOKENS:
            value_expr = func.coalesce(RequestLog.input_tokens, 0)
        elif limit_type == LimitType.OUTPUT_TOKENS:
            value_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
        elif limit_type == LimitType.COST_USD:
            value_expr = cast(func.floor(func.coalesce(RequestLog.cost_usd, 0.0) * 1000000), BigInteger)
        else:
            return 0
        stmt = select(func.coalesce(func.sum(value_expr), 0)).where(
            RequestLog.api_key_id == key_id,
            RequestLog.status == "success",
            self._exclude_warmup_clause(),
            RequestLog.requested_at >= since,
            RequestLog.requested_at < until,
        )
        if model_filter is not None:
            stmt = stmt.where(RequestLog.model == model_filter)
        result = await self._session.execute(stmt)
        value = result.scalar_one()
        return int(value or 0)

    async def release_stale_usage_reservations(
        self,
        *,
        cutoff: datetime,
        max_age_cutoff: datetime | None = None,
        batch_size: int = _STALE_USAGE_RESERVATION_RELEASE_BATCH_SIZE,
    ) -> int:
        released_count = 0

        def _stale_clause(query: Any) -> Any:
            stale = ApiKeyUsageReservation.updated_at < cutoff
            if max_age_cutoff is not None:
                stale = or_(stale, ApiKeyUsageReservation.created_at < max_age_cutoff)
            return query.where(stale)

        try:
            while True:
                async with sqlite_writer_section(self._session):
                    result = await self._session.execute(
                        select(ApiKeyUsageReservation.id)
                        .where(ApiKeyUsageReservation.status == "reserved")
                        .where(ApiKeyUsageReservation.updated_at < cutoff)
                        .order_by(ApiKeyUsageReservation.updated_at.asc())
                        .limit(batch_size)
                    )
                    reservation_ids = list(result.scalars().all())
                    if not reservation_ids:
                        break
                    item_result = await self._session.execute(
                        select(
                            ApiKeyUsageReservationItem.reservation_id,
                            ApiKeyUsageReservationItem.limit_id,
                            ApiKeyUsageReservationItem.limit_type,
                            ApiKeyUsageReservationItem.reserved_delta,
                            ApiKeyUsageReservationItem.expected_reset_at,
                            ApiKeyUsageReservationItem.actual_delta,
                        ).where(ApiKeyUsageReservationItem.reservation_id.in_(reservation_ids))
                    )
                    items_by_reservation_id: dict[str, list[UsageReservationItemData]] = {
                        reservation_id: [] for reservation_id in reservation_ids
                    }
                    for item in item_result.all():
                        items_by_reservation_id[item.reservation_id].append(
                            UsageReservationItemData(
                                limit_id=item.limit_id,
                                limit_type=LimitType(item.limit_type),
                                reserved_delta=item.reserved_delta,
                                expected_reset_at=item.expected_reset_at,
                                actual_delta=item.actual_delta,
                            )
                        )
                    for reservation_id in reservation_ids:
                        claimed = await self._session.execute(
                            _stale_clause(
                                update(ApiKeyUsageReservation)
                                .where(ApiKeyUsageReservation.id == reservation_id)
                                .where(ApiKeyUsageReservation.status == "reserved")
                            )
                            .values(
                                status="released",
                                input_tokens=None,
                                output_tokens=None,
                                cached_input_tokens=None,
                                cost_microdollars=None,
                            )
                            .returning(ApiKeyUsageReservation.id)
                        )
                        if claimed.scalar_one_or_none() is None:
                            continue
                        for item in items_by_reservation_id[reservation_id]:
                            await self.adjust_reserved_usage(
                                item.limit_id, delta=-item.reserved_delta, expected_reset_at=item.expected_reset_at
                            )
                            await self.upsert_reservation_item_actual(
                                reservation_id,
                                item=UsageReservationItemData(
                                    limit_id=item.limit_id,
                                    limit_type=item.limit_type,
                                    reserved_delta=item.reserved_delta,
                                    expected_reset_at=item.expected_reset_at,
                                    actual_delta=item.actual_delta,
                                ),
                                actual_delta=0,
                            )
                        released_count += 1
                    await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return released_count
