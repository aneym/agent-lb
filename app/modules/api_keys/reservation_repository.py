from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.utils.time import utcnow
from app.db.access_models import ApiKeyUsageReservation, ApiKeyUsageReservationItem
from app.db.request_models import ApiKeyLimit, LimitType
from app.modules.api_keys.repository_data import ReservationResult, UsageReservationData, UsageReservationItemData


class ApiKeyReservations:
    _session: AsyncSession

    async def try_reserve_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> ReservationResult:
        if delta <= 0:
            snapshot = await self._session.get(ApiKeyLimit, limit_id)
            return ReservationResult(
                success=True,
                limit_id=limit_id,
                current_value=snapshot.current_value if snapshot is not None else None,
                max_value=snapshot.max_value if snapshot is not None else None,
                reset_at=snapshot.reset_at if snapshot is not None else None,
            )

        result = await self._session.execute(
            update(ApiKeyLimit)
            .where(ApiKeyLimit.id == limit_id)
            .where(ApiKeyLimit.reset_at == expected_reset_at)
            .where(ApiKeyLimit.current_value + delta <= ApiKeyLimit.max_value)
            .values(current_value=ApiKeyLimit.current_value + delta)
            .returning(
                ApiKeyLimit.id,
                ApiKeyLimit.current_value,
                ApiKeyLimit.max_value,
                ApiKeyLimit.reset_at,
            )
        )
        row = result.first()
        if row is not None:
            return ReservationResult(
                success=True,
                limit_id=int(row.id),
                current_value=int(row.current_value),
                max_value=int(row.max_value),
                reset_at=row.reset_at,
            )

        snapshot_result = await self._session.execute(
            select(
                ApiKeyLimit.current_value,
                ApiKeyLimit.max_value,
                ApiKeyLimit.reset_at,
            ).where(ApiKeyLimit.id == limit_id)
        )
        snapshot = snapshot_result.first()
        return ReservationResult(
            success=False,
            limit_id=limit_id,
            current_value=int(snapshot.current_value) if snapshot is not None else None,
            max_value=int(snapshot.max_value) if snapshot is not None else None,
            reset_at=snapshot.reset_at if snapshot is not None else None,
        )

    async def adjust_reserved_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> bool:
        stmt = update(ApiKeyLimit).where(ApiKeyLimit.id == limit_id).where(ApiKeyLimit.reset_at == expected_reset_at)
        if delta < 0:
            stmt = stmt.where(ApiKeyLimit.current_value >= -delta)
        result = await self._session.execute(
            stmt.values(current_value=ApiKeyLimit.current_value + delta).returning(ApiKeyLimit.id)
        )
        return result.scalar_one_or_none() is not None

    async def create_usage_reservation(
        self,
        reservation_id: str,
        *,
        key_id: str,
        model: str,
        items: list[UsageReservationItemData],
    ) -> None:
        reservation = ApiKeyUsageReservation(
            id=reservation_id,
            api_key_id=key_id,
            model=model,
            status="reserved",
        )
        self._session.add(reservation)
        for item in items:
            self._session.add(
                ApiKeyUsageReservationItem(
                    reservation_id=reservation_id,
                    limit_id=item.limit_id,
                    limit_type=item.limit_type.value,
                    reserved_delta=item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                )
            )

    async def get_usage_reservation(self, reservation_id: str) -> UsageReservationData | None:
        result = await self._session.execute(
            select(ApiKeyUsageReservation)
            .options(selectinload(ApiKeyUsageReservation.items))
            .where(ApiKeyUsageReservation.id == reservation_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return UsageReservationData(
            reservation_id=row.id,
            api_key_id=row.api_key_id,
            model=row.model,
            status=row.status,
            items=[
                UsageReservationItemData(
                    limit_id=item.limit_id,
                    limit_type=LimitType(item.limit_type),
                    reserved_delta=item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                    actual_delta=item.actual_delta,
                )
                for item in row.items
            ],
        )

    async def transition_usage_reservation_status(
        self,
        reservation_id: str,
        *,
        expected_status: str,
        new_status: str,
    ) -> bool:
        result = await self._session.execute(
            update(ApiKeyUsageReservation)
            .where(ApiKeyUsageReservation.id == reservation_id)
            .where(ApiKeyUsageReservation.status == expected_status)
            .values(status=new_status)
            .returning(ApiKeyUsageReservation.id)
        )
        return result.scalar_one_or_none() is not None

    async def upsert_reservation_item_actual(
        self,
        reservation_id: str,
        *,
        item: UsageReservationItemData,
        actual_delta: int,
    ) -> None:
        bind = self._session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else "sqlite"
        if dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(ApiKeyUsageReservationItem).values(
                reservation_id=reservation_id,
                limit_id=item.limit_id,
                limit_type=item.limit_type.value,
                reserved_delta=item.reserved_delta,
                expected_reset_at=item.expected_reset_at,
                actual_delta=actual_delta,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    ApiKeyUsageReservationItem.reservation_id,
                    ApiKeyUsageReservationItem.limit_id,
                ],
                set_={
                    "actual_delta": actual_delta,
                    "updated_at": utcnow(),
                },
            )
            await self._session.execute(stmt)
            return
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as postgresql_insert

            stmt = postgresql_insert(ApiKeyUsageReservationItem).values(
                reservation_id=reservation_id,
                limit_id=item.limit_id,
                limit_type=item.limit_type.value,
                reserved_delta=item.reserved_delta,
                expected_reset_at=item.expected_reset_at,
                actual_delta=actual_delta,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    ApiKeyUsageReservationItem.reservation_id,
                    ApiKeyUsageReservationItem.limit_id,
                ],
                set_={
                    "actual_delta": actual_delta,
                    "updated_at": utcnow(),
                },
            )
            await self._session.execute(stmt)
            return
        await self._session.execute(
            update(ApiKeyUsageReservationItem)
            .where(ApiKeyUsageReservationItem.reservation_id == reservation_id)
            .where(ApiKeyUsageReservationItem.limit_id == item.limit_id)
            .values(actual_delta=actual_delta)
        )

    async def settle_usage_reservation(
        self,
        reservation_id: str,
        *,
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_microdollars: int | None,
    ) -> None:
        await self._session.execute(
            update(ApiKeyUsageReservation)
            .where(ApiKeyUsageReservation.id == reservation_id)
            .values(
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cost_microdollars=cost_microdollars,
            )
        )

    async def touch_usage_reservation(self, reservation_id: str) -> bool:
        result = await self._session.execute(
            update(ApiKeyUsageReservation)
            .where(ApiKeyUsageReservation.id == reservation_id)
            .where(ApiKeyUsageReservation.status == "reserved")
            .values(updated_at=utcnow())
            .returning(ApiKeyUsageReservation.id)
        )
        return result.scalar_one_or_none() is not None
