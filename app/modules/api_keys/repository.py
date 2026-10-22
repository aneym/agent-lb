from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.utils.time import utcnow
from app.db.access_models import ApiKeyUsageReservation, ApiKeyUsageReservationItem
from app.db.models import Account, AccountStatus, ApiKey, ApiKeyAccountAssignment, ApiKeyLimit, LimitType, LimitWindow
from app.db.session import sqlite_writer_section
from app.modules.api_keys.limit_windows import advance_limit_reset
from app.modules.api_keys.repository_data import (
    _EXPIRED_LIMIT_RESET_BATCH_SIZE,
    _STALE_USAGE_RESERVATION_RELEASE_BATCH_SIZE,
    _UNSET,
    UsageReservationItemData,
    _Unset,
)
from app.modules.api_keys.reservation_repository import ApiKeyReservations
from app.modules.api_keys.usage_repository import ApiKeyUsageQueries


class ApiKeysRepository(ApiKeyUsageQueries, ApiKeyReservations):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    def _select_api_key(self):
        return (
            select(ApiKey)
            .execution_options(populate_existing=True)
            .options(
                selectinload(ApiKey.limits),
                selectinload(ApiKey.account_assignments),
            )
        )

    async def create(self, row: ApiKey, *, commit: bool = True) -> ApiKey:
        self._session.add(row)
        if commit:
            await self._session.commit()
        created = await self.get_by_id(row.id)
        assert created is not None
        return created

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        result = await self._session.execute(self._select_api_key().where(ApiKey.id == key_id))
        return result.scalar_one_or_none()

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self._session.execute(self._select_api_key().where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[ApiKey]:
        result = await self._session.execute(self._select_api_key().order_by(ApiKey.created_at.desc()))
        return list(result.scalars().unique().all())

    async def list_accounts_by_ids(self, account_ids: list[str]) -> list[Account]:
        if not account_ids:
            return []
        result = await self._session.execute(
            select(Account)
            .options(load_only(Account.id, Account.plan_type, Account.status))
            .where(Account.id.in_(account_ids))
        )
        return list(result.scalars().all())

    async def list_all_accounts(self) -> list[Account]:
        result = await self._session.execute(
            select(Account)
            .options(load_only(Account.id, Account.plan_type, Account.status))
            .where(
                ~Account.status.in_((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED))
            )
        )
        return list(result.scalars().all())

    async def update(
        self,
        key_id: str,
        *,
        name: str | _Unset = _UNSET,
        allowed_models: str | None | _Unset = _UNSET,
        apply_to_codex_model: bool | _Unset = _UNSET,
        enforced_model: str | None | _Unset = _UNSET,
        enforced_reasoning_effort: str | None | _Unset = _UNSET,
        enforced_service_tier: str | None | _Unset = _UNSET,
        traffic_class: str | _Unset = _UNSET,
        account_assignment_scope_enabled: bool | _Unset = _UNSET,
        expires_at: datetime | None | _Unset = _UNSET,
        is_active: bool | _Unset = _UNSET,
        key_hash: str | _Unset = _UNSET,
        key_prefix: str | _Unset = _UNSET,
        member_id: str | None | _Unset = _UNSET,
        commit: bool = True,
    ) -> ApiKey | None:
        row = await self.get_by_id(key_id)
        if row is None:
            return None
        if name is not _UNSET:
            assert isinstance(name, str)
            row.name = name
        if allowed_models is not _UNSET:
            assert allowed_models is None or isinstance(allowed_models, str)
            row.allowed_models = allowed_models
        if apply_to_codex_model is not _UNSET:
            assert isinstance(apply_to_codex_model, bool)
            row.apply_to_codex_model = apply_to_codex_model
        if enforced_model is not _UNSET:
            assert enforced_model is None or isinstance(enforced_model, str)
            row.enforced_model = enforced_model
        if enforced_reasoning_effort is not _UNSET:
            assert enforced_reasoning_effort is None or isinstance(enforced_reasoning_effort, str)
            row.enforced_reasoning_effort = enforced_reasoning_effort
        if enforced_service_tier is not _UNSET:
            assert enforced_service_tier is None or isinstance(enforced_service_tier, str)
            row.enforced_service_tier = enforced_service_tier
        if traffic_class is not _UNSET:
            assert isinstance(traffic_class, str)
            row.traffic_class = traffic_class
        if account_assignment_scope_enabled is not _UNSET:
            assert isinstance(account_assignment_scope_enabled, bool)
            row.account_assignment_scope_enabled = account_assignment_scope_enabled
        if expires_at is not _UNSET:
            assert expires_at is None or isinstance(expires_at, datetime)
            row.expires_at = expires_at
        if is_active is not _UNSET:
            assert isinstance(is_active, bool)
            row.is_active = is_active
        if key_hash is not _UNSET:
            assert isinstance(key_hash, str)
            row.key_hash = key_hash
        if key_prefix is not _UNSET:
            assert isinstance(key_prefix, str)
            row.key_prefix = key_prefix
        if member_id is not _UNSET:
            assert member_id is None or isinstance(member_id, str)
            row.member_id = member_id
        if commit:
            await self._session.commit()
        return await self.get_by_id(key_id)

    async def delete(self, key_id: str) -> bool:
        row = await self.get_by_id(key_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def update_last_used(self, key_id: str, *, commit: bool = True) -> None:
        await self._session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=utcnow()))
        if commit:
            await self._session.commit()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]:
        result = await self._session.execute(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id))
        return list(result.scalars().all())

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]:
        existing = await self.get_limits_by_key(key_id)
        for limit in existing:
            await self._session.delete(limit)
        for limit in limits:
            limit.api_key_id = key_id
            self._session.add(limit)
        await self._session.commit()
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["limits"])
        return await self.get_limits_by_key(key_id)

    async def upsert_limits(self, key_id: str, limits: list[ApiKeyLimit], *, commit: bool = True) -> list[ApiKeyLimit]:
        existing = await self.get_limits_by_key(key_id)
        existing_by_key = {_limit_key(limit): limit for limit in existing}
        incoming_keys = {_limit_key(limit) for limit in limits}

        for incoming in limits:
            key = _limit_key(incoming)
            matched = existing_by_key.get(key)
            if matched is None:
                incoming.api_key_id = key_id
                self._session.add(incoming)
                continue
            matched.max_value = incoming.max_value
            matched.current_value = incoming.current_value
            matched.reset_at = incoming.reset_at

        for old_limit in existing:
            if _limit_key(old_limit) not in incoming_keys:
                await self._session.delete(old_limit)

        if commit:
            await self._session.commit()
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["limits"])
        return await self.get_limits_by_key(key_id)

    async def replace_account_assignments(self, key_id: str, account_ids: list[str], *, commit: bool = True) -> None:
        await self._session.execute(delete(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.api_key_id == key_id))
        for account_id in account_ids:
            self._session.add(ApiKeyAccountAssignment(api_key_id=key_id, account_id=account_id))
        if commit:
            await self._session.commit()
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["account_assignments"])

    async def increment_limit_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_microdollars: int,
    ) -> None:
        limits = await self.get_limits_by_key(key_id)
        for limit in limits:
            if limit.model_filter is not None and limit.model_filter != model:
                continue
            increment = _compute_increment(limit, input_tokens, output_tokens, cost_microdollars)
            if increment > 0:
                await self._session.execute(
                    update(ApiKeyLimit)
                    .where(ApiKeyLimit.id == limit.id)
                    .values(current_value=ApiKeyLimit.current_value + increment)
                )
        await self._session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=utcnow()))
        await self._session.commit()

    async def reset_limit(self, limit_id: int, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool:
        result = await self._session.execute(
            update(ApiKeyLimit)
            .where(ApiKeyLimit.id == limit_id)
            .where(ApiKeyLimit.reset_at == expected_reset_at)
            .values(current_value=0, reset_at=new_reset_at)
            .returning(ApiKeyLimit.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def reset_expired_limits(self, *, now: datetime) -> int:
        reset_count = 0
        while True:
            result = await self._session.execute(
                select(
                    ApiKeyLimit.id,
                    ApiKeyLimit.reset_at,
                    ApiKeyLimit.limit_window,
                )
                .where(ApiKeyLimit.reset_at < now)
                .order_by(ApiKeyLimit.reset_at.asc(), ApiKeyLimit.id.asc())
                .limit(_EXPIRED_LIMIT_RESET_BATCH_SIZE)
            )
            expired_limits = result.all()
            if not expired_limits:
                return reset_count

            for limit in expired_limits:
                update_result = await self._session.execute(
                    update(ApiKeyLimit)
                    .where(ApiKeyLimit.id == limit.id)
                    .where(ApiKeyLimit.reset_at == limit.reset_at)
                    .values(
                        current_value=0,
                        reset_at=advance_limit_reset(limit.reset_at, now, limit.limit_window),
                    )
                    .returning(ApiKeyLimit.id)
                )
                if update_result.scalar_one_or_none() is not None:
                    reset_count += 1
            await self._session.commit()

    async def release_stale_usage_reservations(
        self,
        *,
        cutoff: datetime,
        batch_size: int = _STALE_USAGE_RESERVATION_RELEASE_BATCH_SIZE,
    ) -> int:
        released_count = 0

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
                            update(ApiKeyUsageReservation)
                            .where(ApiKeyUsageReservation.id == reservation_id)
                            .where(ApiKeyUsageReservation.status == "reserved")
                            .where(ApiKeyUsageReservation.updated_at < cutoff)
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
                                item.limit_id,
                                delta=-item.reserved_delta,
                                expected_reset_at=item.expected_reset_at,
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


def _compute_increment(limit: ApiKeyLimit, input_tokens: int, output_tokens: int, cost_microdollars: int) -> int:
    if limit.limit_type == LimitType.TOTAL_TOKENS:
        return input_tokens + output_tokens
    if limit.limit_type == LimitType.INPUT_TOKENS:
        return input_tokens
    if limit.limit_type == LimitType.OUTPUT_TOKENS:
        return output_tokens
    if limit.limit_type == LimitType.COST_USD:
        return cost_microdollars
    return 0


def _limit_key(limit: ApiKeyLimit) -> tuple[LimitType, LimitWindow, str | None]:
    return (limit.limit_type, limit.limit_window, limit.model_filter)
