from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, LimitType, ModelSource
from app.db.request_models import ApiKey, ApiKeyLimit
from app.modules.api_keys.repository_data import (
    ApiKeyTrendBucket,
    ApiKeyUsageSummary,
    ApiKeyUsageTotals,
    ReservationResult,
    UsageReservationData,
    UsageReservationItemData,
    _Unset,
)


class ApiKeysRepositoryProtocol(Protocol):
    @property
    def session(self) -> AsyncSession: ...

    async def create(self, row: ApiKey, *, commit: bool = True) -> ApiKey: ...

    async def get_by_id(self, key_id: str) -> ApiKey | None: ...

    async def get_for_limit_enforcement(self, key_id: str) -> ApiKey | None: ...

    async def get_by_hash(self, key_hash: str) -> ApiKey | None: ...

    async def list_all(self) -> list[ApiKey]: ...

    async def list_usage_summary_by_key(self) -> dict[str, ApiKeyUsageSummary]: ...

    async def get_usage_summary_by_key_id(self, key_id: str) -> ApiKeyUsageSummary: ...

    async def get_limit_usage_value(
        self, key_id: str, *, limit_type: LimitType, since: datetime, until: datetime, model_filter: str | None
    ) -> int: ...

    async def list_accounts_by_ids(self, account_ids: list[str]) -> list[Account]: ...

    async def list_model_sources_by_ids(self, source_ids: list[str]) -> list[ModelSource]: ...

    async def list_all_accounts(self) -> list[Account]: ...

    async def update(
        self,
        key_id: str,
        *,
        name: str | _Unset = ...,
        allowed_models: str | None | _Unset = ...,
        apply_to_codex_model: bool | _Unset = ...,
        enforced_model: str | None | _Unset = ...,
        enforced_reasoning_effort: str | None | _Unset = ...,
        allowed_reasoning_efforts: str | None | _Unset = ...,
        enforced_service_tier: str | None | _Unset = ...,
        traffic_class: str | _Unset = ...,
        transport_policy_override: str | None | _Unset = ...,
        usage_sections: str | _Unset = ...,
        account_assignment_scope_enabled: bool | _Unset = ...,
        source_assignment_scope_enabled: bool | _Unset = ...,
        expires_at: datetime | None | _Unset = ...,
        is_active: bool | _Unset = ...,
        key_hash: str | _Unset = ...,
        key_prefix: str | _Unset = ...,
        member_id: str | None | _Unset = ...,
        commit: bool = True,
    ) -> ApiKey | None: ...

    async def delete(self, key_id: str) -> bool: ...

    async def update_last_used(self, key_id: str, *, commit: bool = True) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def get_limits_by_key(self, key_id: str) -> list[ApiKeyLimit]: ...

    async def replace_limits(self, key_id: str, limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]: ...

    async def upsert_limits(
        self, key_id: str, limits: list[ApiKeyLimit], *, commit: bool = True
    ) -> list[ApiKeyLimit]: ...

    async def replace_account_assignments(
        self, key_id: str, account_ids: list[str], *, commit: bool = True
    ) -> None: ...

    async def replace_source_assignments(self, key_id: str, source_ids: list[str], *, commit: bool = True) -> None: ...

    async def increment_limit_usage(
        self, key_id: str, *, model: str, input_tokens: int, output_tokens: int, cost_microdollars: int
    ) -> None: ...

    async def reset_limit(self, limit_id: int, *, expected_reset_at: datetime, new_reset_at: datetime) -> bool: ...

    async def try_reserve_usage(
        self, limit_id: int, *, delta: int, expected_reset_at: datetime
    ) -> ReservationResult: ...

    async def adjust_reserved_usage(self, limit_id: int, *, delta: int, expected_reset_at: datetime) -> bool: ...

    async def create_usage_reservation(
        self, reservation_id: str, *, key_id: str, model: str, items: list[UsageReservationItemData]
    ) -> None: ...

    async def get_usage_reservation(self, reservation_id: str) -> UsageReservationData | None: ...

    async def transition_usage_reservation_status(
        self, reservation_id: str, *, expected_status: str, new_status: str
    ) -> bool: ...

    async def upsert_reservation_item_actual(
        self, reservation_id: str, *, item: UsageReservationItemData, actual_delta: int
    ) -> None: ...

    async def settle_usage_reservation(
        self,
        reservation_id: str,
        *,
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_microdollars: int | None,
    ) -> None: ...

    async def touch_usage_reservation(self, reservation_id: str) -> bool: ...

    async def trends_by_key(
        self, key_id: str, since: datetime, until: datetime, bucket_seconds: int = 3600
    ) -> list[ApiKeyTrendBucket]: ...

    async def usage_7d(self, key_id: str, since: datetime, until: datetime) -> ApiKeyUsageTotals: ...
