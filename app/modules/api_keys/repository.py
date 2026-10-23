from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, raiseload, selectinload

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKeyModelSourceAssignment, ApiKeyUsageRollup, ModelSource
from app.db.request_models import ApiKey, ApiKeyAccountAssignment, ApiKeyLimit, LimitType, LimitWindow
from app.modules.api_keys.limit_windows import advance_limit_reset
from app.modules.api_keys.repository_data import _EXPIRED_LIMIT_RESET_BATCH_SIZE, _UNSET, _Unset
from app.modules.api_keys.reservation_cleanup import ApiKeyReservationCleanup as ApiKeyReservationCleanup
from app.modules.api_keys.reservation_repository import ApiKeyReservations
from app.modules.api_keys.usage_repository import ApiKeyUsageQueries


class ApiKeysRepository(ApiKeyReservationCleanup, ApiKeyUsageQueries, ApiKeyReservations):
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
                selectinload(ApiKey.source_assignments),
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

    async def get_for_limit_enforcement(self, key_id: str) -> ApiKey | None:
        result = await self._session.execute(
            select(ApiKey)
            .execution_options(populate_existing=True)
            .options(
                load_only(ApiKey.is_active, ApiKey.expires_at, raiseload=True),
                selectinload(ApiKey.limits),
                raiseload(ApiKey.account_assignments),
                raiseload(ApiKey.source_assignments),
            )
            .where(ApiKey.id == key_id)
        )
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
            .where(Account.delete_requested_at.is_(None))
        )
        return list(result.scalars().all())

    async def list_model_sources_by_ids(self, source_ids: list[str]) -> list[ModelSource]:
        if not source_ids:
            return []
        result = await self._session.execute(select(ModelSource).where(ModelSource.id.in_(source_ids)))
        return list(result.scalars().all())

    async def list_all_accounts(self) -> list[Account]:
        result = await self._session.execute(
            select(Account)
            .options(load_only(Account.id, Account.plan_type, Account.status))
            .where(
                ~Account.status.in_((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED))
            )
            .where(Account.delete_requested_at.is_(None))
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
        allowed_reasoning_efforts: str | None | _Unset = _UNSET,
        transport_policy_override: str | None | _Unset = _UNSET,
        usage_sections: str | _Unset = _UNSET,
        source_assignment_scope_enabled: bool | _Unset = _UNSET,
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
        if allowed_reasoning_efforts is not _UNSET:
            assert allowed_reasoning_efforts is None or isinstance(allowed_reasoning_efforts, str)
            row.allowed_reasoning_efforts = allowed_reasoning_efforts
        if enforced_service_tier is not _UNSET:
            assert enforced_service_tier is None or isinstance(enforced_service_tier, str)
            row.enforced_service_tier = enforced_service_tier
        if traffic_class is not _UNSET:
            assert isinstance(traffic_class, str)
            row.traffic_class = traffic_class
        if transport_policy_override is not _UNSET:
            assert transport_policy_override is None or isinstance(transport_policy_override, str)
            row.transport_policy_override = transport_policy_override
        if usage_sections is not _UNSET:
            assert isinstance(usage_sections, str)
            row.usage_sections = usage_sections
        if account_assignment_scope_enabled is not _UNSET:
            assert isinstance(account_assignment_scope_enabled, bool)
            row.account_assignment_scope_enabled = account_assignment_scope_enabled
        if source_assignment_scope_enabled is not _UNSET:
            assert isinstance(source_assignment_scope_enabled, bool)
            row.source_assignment_scope_enabled = source_assignment_scope_enabled
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
        await self._session.execute(delete(ApiKeyUsageRollup).where(ApiKeyUsageRollup.api_key_id == key_id))
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def commit(self) -> None:
        await self._session.commit()

    async def update_last_used(self, key_id: str, *, commit: bool = True) -> None:
        await self._session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=utcnow()))
        if commit:
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
        if account_ids and self._session.get_bind().dialect.name == "postgresql":
            await self._session.execute(
                select(Account.id).where(Account.id.in_(account_ids)).with_for_update(read=True)
            )
        await self._session.execute(delete(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.api_key_id == key_id))
        if account_ids:
            assignment_source = (
                select(literal(key_id), Account.id)
                .where(Account.id.in_(account_ids))
                .where(Account.delete_requested_at.is_(None))
            )
            await self._session.execute(
                insert(ApiKeyAccountAssignment).from_select(["api_key_id", "account_id"], assignment_source)
            )
        if commit:
            await self._session.commit()
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["account_assignments"])

    async def replace_source_assignments(self, key_id: str, source_ids: list[str], *, commit: bool = True) -> None:
        await self._session.execute(
            delete(ApiKeyModelSourceAssignment).where(ApiKeyModelSourceAssignment.api_key_id == key_id)
        )
        for source_id in source_ids:
            self._session.add(ApiKeyModelSourceAssignment(api_key_id=key_id, source_id=source_id))
        if commit:
            await self._session.commit()
        parent = await self._session.get(ApiKey, key_id)
        if parent is not None:
            await self._session.refresh(parent, attribute_names=["source_assignments"])

    async def increment_limit_usage(
        self, key_id: str, *, model: str, input_tokens: int, output_tokens: int, cost_microdollars: int
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
                select(ApiKeyLimit.id, ApiKeyLimit.reset_at, ApiKeyLimit.limit_window)
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
                    .values(current_value=0, reset_at=advance_limit_reset(limit.reset_at, now, limit.limit_window))
                    .returning(ApiKeyLimit.id)
                )
                if update_result.scalar_one_or_none() is not None:
                    reset_count += 1
            await self._session.commit()


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
