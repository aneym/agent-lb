from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import Account, ApiKey, ApiKeyAccountAssignment, ApiKeyLimit, LimitType, UsageHistory
from app.modules.api_keys.repository import (
    _UNSET,
    ApiKeyUsageSummary,
    ReservationResult,
    UsageReservationData,
    UsageReservationItemData,
    _Unset,
)
from app.modules.api_keys.service import (
    ApiKeyCreateData,
    ApiKeysRepositoryProtocol,
    ApiKeysService,
    LimitRuleInput,
)
from app.modules.usage.repository import UsageRepository


class _FakeApiKeysRepository(ApiKeysRepositoryProtocol):
    def __init__(self) -> None:
        self._session = AsyncMock(spec=AsyncSession)
        self.rows: dict[str, ApiKey] = {}
        self._limits: dict[str, list[ApiKeyLimit]] = {}
        self._account_assignments: dict[str, list[ApiKeyAccountAssignment]] = {}
        self._accounts: dict[str, Account] = {}
        self._limit_id_seq = 0
        self._reservations: dict[str, UsageReservationData] = {}
        self.list_all_accounts_calls = 0
        self.list_accounts_by_ids_calls: list[list[str]] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.commit_count = 0
        self.update_last_used_commit_flags: list[bool] = []
        self.touched_reservations: list[str] = []

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def create(self, row: ApiKey, *, commit: bool = True) -> ApiKey:
        del commit
        self.rows[row.id] = row
        row.limits = []
        row.account_assignments = []
        return row

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        row = self.rows.get(key_id)
        if row is not None:
            row.limits = self._limits.get(key_id, [])
            row.account_assignments = self._account_assignments.get(key_id, [])
        return row

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        for row in self.rows.values():
            if row.key_hash == key_hash:
                row.limits = self._limits.get(row.id, [])
                row.account_assignments = self._account_assignments.get(row.id, [])
                return row
        return None

    async def list_all(self) -> list[ApiKey]:
        result = sorted(self.rows.values(), key=lambda row: row.created_at, reverse=True)
        for row in result:
            row.limits = self._limits.get(row.id, [])
            row.account_assignments = self._account_assignments.get(row.id, [])
        return result

    async def list_accounts_by_ids(self, account_ids: list[str]) -> list[Account]:
        self.list_accounts_by_ids_calls.append(list(account_ids))
        return [self._accounts[account_id] for account_id in account_ids if account_id in self._accounts]

    async def list_all_accounts(self) -> list[Account]:
        self.list_all_accounts_calls += 1
        return list(self._accounts.values())

    async def list_usage_summary_by_key(self) -> dict[str, ApiKeyUsageSummary]:
        return {}

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
        del commit
        row = self.rows.get(key_id)
        if row is None:
            return None
        for field, value in {
            "name": name,
            "allowed_models": allowed_models,
            "apply_to_codex_model": apply_to_codex_model,
            "enforced_model": enforced_model,
            "enforced_reasoning_effort": enforced_reasoning_effort,
            "enforced_service_tier": enforced_service_tier,
            "traffic_class": traffic_class,
            "account_assignment_scope_enabled": account_assignment_scope_enabled,
            "expires_at": expires_at,
            "is_active": is_active,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "member_id": member_id,
        }.items():
            if value is _UNSET:
                continue
            setattr(row, field, value)
        row.limits = self._limits.get(key_id, [])
        return row

    async def delete(self, key_id: str) -> bool:
        if key_id not in self.rows:
            return False
        self.rows.pop(key_id)
        self._limits.pop(key_id, None)
        return True

    async def update_last_used(self, key_id: str, *, commit: bool = True) -> None:
        self.update_last_used_commit_flags.append(commit)
        row = self.rows.get(key_id)
        if row is not None:
            row.last_used_at = utcnow()
        if commit:
            await self.commit()

    async def commit(self) -> None:
        self.commit_calls += 1
        self.commit_count += 1
        return None

    async def rollback(self) -> None:
        self.rollback_calls += 1
        return None

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]:
        return list(self._limits.get(key_id, []))

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]:
        for limit in limits:
            self._limit_id_seq += 1
            limit.id = self._limit_id_seq
            limit.api_key_id = key_id
        self._limits[key_id] = list(limits)
        row = self.rows.get(key_id)
        if row is not None:
            row.limits = self._limits[key_id]
        return self._limits[key_id]

    async def upsert_limits(self, key_id: str, limits: list[ApiKeyLimit], *, commit: bool = True) -> list[ApiKeyLimit]:
        del commit
        existing = self._limits.get(key_id, [])
        existing_by_key = {(limit.limit_type, limit.limit_window, limit.model_filter): limit for limit in existing}

        updated: list[ApiKeyLimit] = []
        for incoming in limits:
            key = (incoming.limit_type, incoming.limit_window, incoming.model_filter)
            matched = existing_by_key.get(key)
            if matched is not None:
                matched.max_value = incoming.max_value
                matched.current_value = incoming.current_value
                matched.reset_at = incoming.reset_at
                updated.append(matched)
                continue
            self._limit_id_seq += 1
            incoming.id = self._limit_id_seq
            incoming.api_key_id = key_id
            updated.append(incoming)

        self._limits[key_id] = updated
        row = self.rows.get(key_id)
        if row is not None:
            row.limits = updated
        return updated

    async def replace_account_assignments(self, key_id: str, account_ids: list[str], *, commit: bool = True) -> None:
        del commit
        assignments = [ApiKeyAccountAssignment(api_key_id=key_id, account_id=account_id) for account_id in account_ids]
        self._account_assignments[key_id] = assignments
        row = self.rows.get(key_id)
        if row is not None:
            row.account_assignments = assignments

    async def increment_limit_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_microdollars: int,
    ) -> None:
        limits = self._limits.get(key_id, [])
        for limit in limits:
            if limit.model_filter is not None and limit.model_filter != model:
                continue
            increment = _compute_increment(limit, input_tokens, output_tokens, cost_microdollars)
            if increment > 0:
                limit.current_value += increment
        row = self.rows.get(key_id)
        if row is not None:
            row.last_used_at = utcnow()

    async def reset_limit(self, limit_id: int, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool:
        for limits in self._limits.values():
            for limit in limits:
                if limit.id == limit_id and limit.reset_at == expected_reset_at:
                    limit.current_value = 0
                    limit.reset_at = new_reset_at
                    return True
        return False

    async def try_reserve_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> ReservationResult:
        limit = _find_limit_by_id(self._limits, limit_id)
        if limit is None:
            return ReservationResult(False, limit_id, None, None, None)
        if limit.reset_at != expected_reset_at:
            return ReservationResult(False, limit_id, limit.current_value, limit.max_value, limit.reset_at)
        if limit.current_value + delta > limit.max_value:
            return ReservationResult(False, limit_id, limit.current_value, limit.max_value, limit.reset_at)
        limit.current_value += delta
        return ReservationResult(True, limit_id, limit.current_value, limit.max_value, limit.reset_at)

    async def adjust_reserved_usage(
        self,
        limit_id: int,
        *,
        delta: int,
        expected_reset_at: datetime,
    ) -> bool:
        limit = _find_limit_by_id(self._limits, limit_id)
        if limit is None or limit.reset_at != expected_reset_at:
            return False
        next_value = limit.current_value + delta
        if next_value < 0:
            return False
        limit.current_value = next_value
        return True

    async def create_usage_reservation(
        self,
        reservation_id: str,
        *,
        key_id: str,
        model: str,
        items: list[UsageReservationItemData],
    ) -> None:
        self._reservations[reservation_id] = UsageReservationData(
            reservation_id=reservation_id,
            api_key_id=key_id,
            model=model,
            status="reserved",
            items=[
                UsageReservationItemData(
                    limit_id=item.limit_id,
                    limit_type=item.limit_type,
                    reserved_delta=item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                    actual_delta=item.actual_delta,
                )
                for item in items
            ],
        )

    async def get_usage_reservation(self, reservation_id: str) -> UsageReservationData | None:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            return None
        return UsageReservationData(
            reservation_id=reservation.reservation_id,
            api_key_id=reservation.api_key_id,
            model=reservation.model,
            status=reservation.status,
            items=[
                UsageReservationItemData(
                    limit_id=item.limit_id,
                    limit_type=item.limit_type,
                    reserved_delta=item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                    actual_delta=item.actual_delta,
                )
                for item in reservation.items
            ],
        )

    async def transition_usage_reservation_status(
        self,
        reservation_id: str,
        *,
        expected_status: str,
        new_status: str,
    ) -> bool:
        reservation = self._reservations.get(reservation_id)
        if reservation is None or reservation.status != expected_status:
            return False
        self._reservations[reservation_id] = UsageReservationData(
            reservation_id=reservation.reservation_id,
            api_key_id=reservation.api_key_id,
            model=reservation.model,
            status=new_status,
            items=reservation.items,
        )
        return True

    async def upsert_reservation_item_actual(
        self,
        reservation_id: str,
        *,
        item: UsageReservationItemData,
        actual_delta: int,
    ) -> None:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            return
        updated_items: list[UsageReservationItemData] = []
        found = False
        for existing in reservation.items:
            if existing.limit_id == item.limit_id:
                updated_items.append(
                    UsageReservationItemData(
                        limit_id=existing.limit_id,
                        limit_type=existing.limit_type,
                        reserved_delta=existing.reserved_delta,
                        expected_reset_at=existing.expected_reset_at,
                        actual_delta=actual_delta,
                    )
                )
                found = True
            else:
                updated_items.append(existing)
        if not found:
            updated_items.append(
                UsageReservationItemData(
                    limit_id=item.limit_id,
                    limit_type=item.limit_type,
                    reserved_delta=item.reserved_delta,
                    expected_reset_at=item.expected_reset_at,
                    actual_delta=actual_delta,
                )
            )
        self._reservations[reservation_id] = UsageReservationData(
            reservation_id=reservation.reservation_id,
            api_key_id=reservation.api_key_id,
            model=reservation.model,
            status=reservation.status,
            items=updated_items,
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
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            return
        self._reservations[reservation_id] = UsageReservationData(
            reservation_id=reservation.reservation_id,
            api_key_id=reservation.api_key_id,
            model=reservation.model,
            status=status,
            items=reservation.items,
        )

    async def touch_usage_reservation(self, reservation_id: str) -> bool:
        reservation = self._reservations.get(reservation_id)
        if reservation is None or reservation.status != "reserved":
            return False
        self.touched_reservations.append(reservation_id)
        return True


class _FakeUsageRepository(UsageRepository):
    def __init__(
        self,
        *,
        primary: dict[str, UsageHistory],
        secondary: dict[str, UsageHistory],
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self.calls: list[tuple[str | None, list[str] | None]] = []

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        self.calls.append((window, None if account_ids is None else list(account_ids)))
        source = self._secondary if window == "secondary" else self._primary
        rows = dict(source)
        if account_ids is not None:
            allowed = set(account_ids)
            rows = {account_id: row for account_id, row in rows.items() if account_id in allowed}
        return rows


class _TransactionalAssignmentFailureRepo(_FakeApiKeysRepository):
    def __init__(self) -> None:
        super().__init__()
        self._pending_rows: dict[str, ApiKey] = {}
        self._pending_account_assignments: dict[str, list[ApiKeyAccountAssignment]] = {}

    async def create(self, row: ApiKey, *, commit: bool = True) -> ApiKey:
        row.limits = []
        row.account_assignments = []
        if commit:
            return await super().create(row, commit=commit)
        self._pending_rows[row.id] = row
        return row

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        row = self.rows.get(key_id)
        if row is not None:
            row.limits = self._limits.get(key_id, [])
            row.account_assignments = self._account_assignments.get(key_id, [])
        return row

    async def replace_account_assignments(self, key_id: str, account_ids: list[str], *, commit: bool = True) -> None:
        del account_ids, commit
        raise RuntimeError("assignment insert failed")

    async def commit(self) -> None:
        self.commit_calls += 1
        self.rows.update(self._pending_rows)
        self._account_assignments.update(self._pending_account_assignments)
        self._pending_rows = {}
        self._pending_account_assignments = {}

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self._pending_rows = {}
        self._pending_account_assignments = {}


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


def _find_limit_by_id(
    limits_by_key: dict[str, list[ApiKeyLimit]],
    limit_id: int,
) -> ApiKeyLimit | None:
    for limits in limits_by_key.values():
        for limit in limits:
            if limit.id == limit_id:
                return limit
    return None


async def _async_noop(*args, **kwargs) -> None:
    del args, kwargs


def _make_usage_history(
    account_id: str,
    *,
    used_percent: float,
    reset_at: int | None = None,
    window_minutes: int | None = None,
    recorded_at: datetime | None = None,
) -> UsageHistory:
    return UsageHistory(
        account_id=account_id,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes,
        recorded_at=recorded_at or utcnow(),
    )


async def _cost_capped_key(service: ApiKeysService, name: str, *, max_value: int = 10_000_000) -> str:
    created = await service.create_key(
        ApiKeyCreateData(
            name=name,
            allowed_models=None,
            expires_at=None,
            limits=[LimitRuleInput(limit_type="cost_usd", limit_window="daily", max_value=max_value)],
        )
    )
    return created.id
