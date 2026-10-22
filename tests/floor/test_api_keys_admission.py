from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError

from app.core.utils.time import utcnow
from app.db.models import ApiKey, LimitType
from app.modules.api_keys.repository_data import UsageReservationItemData
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyCreateData, ApiKeyInvalidError, ApiKeyRateLimitExceededError, ApiKeyRequestUsageBudget, LimitRuleInput  # noqa: I001  # fmt: skip
from tests.floor.api_keys_service_support import _async_noop, _FakeApiKeysRepository  # noqa: I001  # fmt: skip

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_create_key_with_limits() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)

    created = await service.create_key(
        ApiKeyCreateData(
            name="limited-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(limit_type="cost_usd", limit_window="daily", max_value=5_000_000),
            ],
        )
    )

    assert len(created.limits) == 2
    token_limit = next(lim for lim in created.limits if lim.limit_type == "total_tokens")
    cost_limit = next(lim for lim in created.limits if lim.limit_type == "cost_usd")
    assert token_limit.max_value == 1_000_000
    assert token_limit.limit_window == "weekly"
    assert token_limit.current_value == 0
    assert cost_limit.max_value == 5_000_000
    assert cost_limit.limit_window == "daily"


@pytest.mark.asyncio
async def test_validate_key_checks_expiry_and_limit() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="limited-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10),
            ],
        )
    )

    limits = await repo.get_limits_by_key(created.id)
    limits[0].current_value = 10
    limits[0].reset_at = utcnow() + timedelta(days=1)
    validated = await service.validate_key(created.key)
    assert validated.id == created.id

    with pytest.raises(ApiKeyRateLimitExceededError):
        await service.enforce_limits_for_request(created.id, request_model="gpt-5")

    limits[0].current_value = 5
    row = await repo.get_by_id(created.id)
    assert row is not None
    row.expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(ApiKeyInvalidError):
        await service.validate_key(created.key)


@pytest.mark.asyncio
async def test_validate_key_lazy_resets_expired_limit() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reset-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10),
            ],
        )
    )

    limits = await repo.get_limits_by_key(created.id)
    limits[0].current_value = 9
    limits[0].reset_at = utcnow() - timedelta(days=8)

    validated = await service.validate_key(created.key)
    assert validated.id == created.id

    updated_limits = await repo.get_limits_by_key(created.id)
    assert updated_limits[0].current_value == 0
    assert updated_limits[0].reset_at > utcnow()


@pytest.mark.asyncio
async def test_validate_key_does_not_refetch_when_limits_do_not_need_reset() -> None:
    class _CountingRepo(_FakeApiKeysRepository):
        def __init__(self) -> None:
            super().__init__()
            self.get_by_hash_calls = 0

        async def get_by_hash(self, key_hash: str) -> ApiKey | None:
            self.get_by_hash_calls += 1
            return await super().get_by_hash(key_hash)

    repo = _CountingRepo()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="single-fetch",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10),
            ],
        )
    )

    validated = await service.validate_key(created.key)

    assert validated.id == created.id
    assert repo.get_by_hash_calls == 1


@pytest.mark.asyncio
async def test_validate_key_advances_reset_strictly_into_future(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="boundary-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10),
            ],
        )
    )
    fixed_now = utcnow()
    monkeypatch.setattr("app.modules.api_keys.service.utcnow", lambda: fixed_now)

    limits = await repo.get_limits_by_key(created.id)
    limits[0].current_value = 7
    limits[0].reset_at = fixed_now - timedelta(days=14)

    await service.validate_key(created.key)

    updated_limits = await repo.get_limits_by_key(created.id)
    assert updated_limits[0].current_value == 0
    assert updated_limits[0].reset_at == fixed_now + timedelta(days=7)


@pytest.mark.asyncio
async def test_validate_key_multi_limit_all_must_pass() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="multi-limit-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
                LimitRuleInput(limit_type="cost_usd", limit_window="daily", max_value=5_000_000),
            ],
        )
    )

    limits = await repo.get_limits_by_key(created.id)
    token_limit = next(lim for lim in limits if lim.limit_type == LimitType.TOTAL_TOKENS)
    cost_limit = next(lim for lim in limits if lim.limit_type == LimitType.COST_USD)

    token_limit.current_value = 50
    cost_limit.current_value = 5_000_000
    token_limit.reset_at = utcnow() + timedelta(days=1)
    cost_limit.reset_at = utcnow() + timedelta(days=1)

    with pytest.raises(ApiKeyRateLimitExceededError) as exc_info:
        await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")
    assert "cost_usd" in str(exc_info.value)


@pytest.mark.asyncio
async def test_enforce_limits_reserves_tier_aware_cost_budget() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    priority_created = await service.create_key(
        ApiKeyCreateData(
            name="priority-cost-reserve-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="cost_usd", limit_window="weekly", max_value=1_000_000),
            ],
        )
    )

    priority_reservation = await service.enforce_limits_for_request(
        priority_created.id,
        request_model="gpt-5.4",
        request_service_tier="priority",
        request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=8192, output_tokens=8192),
    )
    assert priority_reservation.key_id == priority_created.id

    priority_limits = await repo.get_limits_by_key(priority_created.id)
    priority_cost_limit = next(lim for lim in priority_limits if lim.limit_type == LimitType.COST_USD)
    assert priority_cost_limit.current_value == 286_720

    standard_created = await service.create_key(
        ApiKeyCreateData(
            name="standard-cost-reserve-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="cost_usd", limit_window="weekly", max_value=1_000_000),
            ],
        )
    )
    standard_reservation = await service.enforce_limits_for_request(
        standard_created.id,
        request_model="gpt-5.4",
        request_service_tier=None,
        request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=8192, output_tokens=8192),
    )
    assert standard_reservation.key_id == standard_created.id

    standard_limits = await repo.get_limits_by_key(standard_created.id)
    standard_cost_limit = next(lim for lim in standard_limits if lim.limit_type == LimitType.COST_USD)
    assert standard_cost_limit.current_value == 143_360


def test_api_key_request_usage_budget_rejects_non_integer_tokens() -> None:
    with pytest.raises(TypeError, match="input_tokens"):
        ApiKeyRequestUsageBudget(input_tokens=cast(Any, "128"))
    with pytest.raises(TypeError, match="output_tokens"):
        ApiKeyRequestUsageBudget(output_tokens=cast(Any, True))


@pytest.mark.asyncio
async def test_enforce_limits_default_budget_allows_eight_priority_lanes_under_five_dollars() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="priority-lanes",
            allowed_models=None,
            expires_at=None,
            limits=[LimitRuleInput(limit_type="cost_usd", limit_window="weekly", max_value=5_000_000)],
        )
    )

    reservations = await asyncio.gather(
        *(
            service.enforce_limits_for_request(
                created.id,
                request_model="gpt-5.5",
                request_service_tier="priority",
            )
            for _ in range(8)
        )
    )

    assert len(reservations) == 8
    assert {reservation.key_id for reservation in reservations} == {created.id}
    limits = await repo.get_limits_by_key(created.id)
    cost_limit = next(lim for lim in limits if lim.limit_type == LimitType.COST_USD)
    assert 0 < cost_limit.current_value < 5_000_000


@pytest.mark.asyncio
async def test_enforce_limits_request_budget_bounds_token_and_cost_reservations() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="bounded-request",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="input_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(limit_type="output_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(limit_type="cost_usd", limit_window="weekly", max_value=1_000_000),
            ],
        )
    )

    await service.enforce_limits_for_request(
        created.id,
        request_model="gpt-5.5",
        request_service_tier="priority",
        request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=123, output_tokens=456),
    )

    limits = await repo.get_limits_by_key(created.id)
    by_type = {limit.limit_type: limit.current_value for limit in limits}
    assert by_type[LimitType.INPUT_TOKENS] == 123
    assert by_type[LimitType.OUTPUT_TOKENS] == 456
    assert by_type[LimitType.TOTAL_TOKENS] == 579
    assert 0 < by_type[LimitType.COST_USD] < 1_000_000


@pytest.mark.asyncio
async def test_finalize_usage_reservation_accounts_for_zero_reserved_limit_item() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="zero-output-reserve",
            allowed_models=None,
            expires_at=None,
            limits=[LimitRuleInput(limit_type="output_tokens", limit_window="weekly", max_value=1_000)],
        )
    )

    reservation = await service.enforce_limits_for_request(
        created.id,
        request_model="gpt-5.5",
        request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=0, output_tokens=0),
    )

    limits = await repo.get_limits_by_key(created.id)
    output_limit = next(limit for limit in limits if limit.limit_type == LimitType.OUTPUT_TOKENS)
    assert output_limit.current_value == 0

    await service.finalize_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.5",
        input_tokens=0,
        output_tokens=100,
    )

    assert output_limit.current_value == 100


@pytest.mark.asyncio
async def test_enforce_limits_retries_sqlite_busy_reservation_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BusyRepo(_FakeApiKeysRepository):
        def __init__(self) -> None:
            super().__init__()
            self.create_usage_reservation_calls = 0

        async def create_usage_reservation(
            self,
            reservation_id: str,
            *,
            key_id: str,
            model: str,
            items: list[UsageReservationItemData],
        ) -> None:
            self.create_usage_reservation_calls += 1
            if self.create_usage_reservation_calls < 3:
                raise OperationalError("insert usage reservation", {}, Exception("database is locked"))
            await super().create_usage_reservation(
                reservation_id,
                key_id=key_id,
                model=model,
                items=items,
            )

    repo = _BusyRepo()
    service = ApiKeysService(repo)
    monkeypatch.setattr("app.modules.api_keys.reservation_service.asyncio.sleep", _async_noop)
    created = await service.create_key(ApiKeyCreateData(name="busy-retry-key", allowed_models=None, expires_at=None))
    initial_commit_count = repo.commit_count

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")

    assert reservation.key_id == created.id
    assert repo.create_usage_reservation_calls == 3
    assert repo.commit_count == initial_commit_count + 1


@pytest.mark.asyncio
async def test_enforce_limits_retries_sqlite_busy_during_lazy_reset_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BusyRepo(_FakeApiKeysRepository):
        def __init__(self) -> None:
            super().__init__()
            self.reset_limit_calls = 0
            self.rollback_calls = 0

        async def reset_limit(
            self,
            limit_id: int,
            *,
            expected_reset_at: datetime,
            new_reset_at: datetime,
        ) -> bool:
            self.reset_limit_calls += 1
            if self.reset_limit_calls < 3:
                raise OperationalError("reset expired limit", {}, Exception("database is locked"))
            return await super().reset_limit(limit_id, expected_reset_at=expected_reset_at, new_reset_at=new_reset_at)

        async def rollback(self) -> None:
            self.rollback_calls += 1
            await super().rollback()

    repo = _BusyRepo()
    service = ApiKeysService(repo)
    monkeypatch.setattr("app.modules.api_keys.reservation_service.asyncio.sleep", _async_noop)
    created = await service.create_key(
        ApiKeyCreateData(
            name="busy-lazy-reset-retry-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=10_000),
            ],
        )
    )
    initial_commit_count = repo.commit_count

    limits = await repo.get_limits_by_key(created.id)
    limits[0].current_value = 0
    limits[0].reset_at = utcnow() - timedelta(days=8)

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5")

    assert reservation.key_id == created.id
    assert repo.reset_limit_calls == 3
    assert repo.rollback_calls >= 2
    assert repo.commit_count == initial_commit_count + 1
