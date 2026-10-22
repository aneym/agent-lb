from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.db.models import LimitType
from app.modules.api_keys.repository_data import UsageReservationData
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyCreateData, LimitRuleInput
from tests.floor.api_keys_service_support import _async_noop, _FakeApiKeysRepository  # noqa: I001  # fmt: skip

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_record_usage_increments_matching_limits() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="usage-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(limit_type="input_tokens", limit_window="weekly", max_value=500_000),
                LimitRuleInput(limit_type="output_tokens", limit_window="weekly", max_value=500_000),
            ],
        )
    )

    await service.record_usage(
        created.id,
        model="test-model",
        input_tokens=100,
        output_tokens=50,
        cached_input_tokens=20,
    )

    limits = await repo.get_limits_by_key(created.id)
    total_limit = next(lim for lim in limits if lim.limit_type == LimitType.TOTAL_TOKENS)
    input_limit = next(lim for lim in limits if lim.limit_type == LimitType.INPUT_TOKENS)
    output_limit = next(lim for lim in limits if lim.limit_type == LimitType.OUTPUT_TOKENS)

    assert total_limit.current_value == 150
    assert input_limit.current_value == 100
    assert output_limit.current_value == 50


@pytest.mark.asyncio
async def test_record_usage_model_filter_matching() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="model-filter-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000_000),
                LimitRuleInput(
                    limit_type="total_tokens", limit_window="weekly", max_value=500_000, model_filter="gpt-5.1"
                ),
            ],
        )
    )

    await service.record_usage(
        created.id,
        model="gpt-5.1",
        input_tokens=100,
        output_tokens=50,
    )

    limits = await repo.get_limits_by_key(created.id)
    global_limit = next(lim for lim in limits if lim.model_filter is None)
    model_limit = next(lim for lim in limits if lim.model_filter == "gpt-5.1")
    assert global_limit.current_value == 150
    assert model_limit.current_value == 150

    await service.record_usage(
        created.id,
        model="gpt-4o-mini",
        input_tokens=200,
        output_tokens=100,
    )

    limits = await repo.get_limits_by_key(created.id)
    global_limit = next(lim for lim in limits if lim.model_filter is None)
    model_limit = next(lim for lim in limits if lim.model_filter == "gpt-5.1")
    assert global_limit.current_value == 450
    assert model_limit.current_value == 150


@pytest.mark.asyncio
async def test_record_usage_cost_limit_uses_service_tier_pricing() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="priority-cost-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="cost_usd", limit_window="weekly", max_value=100_000_000),
            ],
        )
    )

    await service.record_usage(
        created.id,
        model="gpt-5.4",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        service_tier="priority",
    )

    limits = await repo.get_limits_by_key(created.id)
    cost_limit = next(lim for lim in limits if lim.limit_type == LimitType.COST_USD)
    assert cost_limit.current_value == 35_000_000


@pytest.mark.asyncio
async def test_record_usage_cost_limit_uses_legacy_gpt_5_priority_pricing() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="legacy-priority-cost-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="cost_usd", limit_window="weekly", max_value=100_000_000),
            ],
        )
    )

    await service.record_usage(
        created.id,
        model="gpt-5.1",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        service_tier="priority",
    )

    limits = await repo.get_limits_by_key(created.id)
    cost_limit = next(lim for lim in limits if lim.limit_type == LimitType.COST_USD)
    assert cost_limit.current_value == 22_500_000


@pytest.mark.asyncio
async def test_record_usage_cost_limit_uses_flex_service_tier_pricing() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="flex-cost-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="cost_usd", limit_window="weekly", max_value=100_000_000),
            ],
        )
    )

    await service.record_usage(
        created.id,
        model="gpt-5.4-mini",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        service_tier="flex",
    )

    limits = await repo.get_limits_by_key(created.id)
    cost_limit = next(lim for lim in limits if lim.limit_type == LimitType.COST_USD)
    assert cost_limit.current_value == 2_625_000


@pytest.mark.asyncio
async def test_release_usage_reservation_restores_reserved_counter() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reservation-release-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")
    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 100

    await service.release_usage_reservation(reservation.reservation_id)
    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 0


@pytest.mark.asyncio
async def test_touch_usage_reservation_only_updates_reserved_reservation() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reservation-touch-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")

    assert await service.touch_usage_reservation(reservation.reservation_id) is True
    await service.release_usage_reservation(reservation.reservation_id)
    assert await service.touch_usage_reservation(reservation.reservation_id) is False
    assert repo.touched_reservations == [reservation.reservation_id]


@pytest.mark.asyncio
async def test_finalize_usage_reservation_is_idempotent() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reservation-finalize-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")
    await service.finalize_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.1",
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=0,
    )
    await service.finalize_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.1",
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=0,
    )

    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 15


@pytest.mark.asyncio
async def test_finalize_usage_reservation_updates_last_used_in_settlement_commit() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reservation-last-used-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )
    initial_commit_count = repo.commit_count

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")
    assert repo.commit_count == initial_commit_count + 1

    await service.finalize_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.1",
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=0,
    )

    stored = await repo.get_by_id(created.id)
    assert stored is not None
    assert stored.last_used_at is not None
    assert repo.update_last_used_commit_flags == [False]
    assert repo.commit_count == initial_commit_count + 2


@pytest.mark.asyncio
async def test_finalize_usage_reservation_retries_sqlite_busy_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BusyRepo(_FakeApiKeysRepository):
        def __init__(self) -> None:
            super().__init__()
            self.get_usage_reservation_calls = 0

        async def get_usage_reservation(self, reservation_id: str) -> UsageReservationData | None:
            self.get_usage_reservation_calls += 1
            if self.get_usage_reservation_calls < 3:
                raise OperationalError("settle usage reservation", {}, Exception("database is locked"))
            return await super().get_usage_reservation(reservation_id)

    repo = _BusyRepo()
    service = ApiKeysService(repo)
    monkeypatch.setattr("app.modules.api_keys.reservation_service.asyncio.sleep", _async_noop)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reservation-finalize-busy-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )
    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")

    await service.finalize_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.1",
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=0,
    )

    stored = await repo.get_usage_reservation(reservation.reservation_id)
    limits = await repo.get_limits_by_key(created.id)
    assert stored is not None
    assert stored.status == "finalized"
    assert limits[0].current_value == 15
    assert repo.get_usage_reservation_calls == 4
    assert repo.rollback_calls >= 2


@pytest.mark.asyncio
async def test_release_usage_reservation_retries_sqlite_busy_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BusyRepo(_FakeApiKeysRepository):
        def __init__(self) -> None:
            super().__init__()
            self.get_usage_reservation_calls = 0

        async def get_usage_reservation(self, reservation_id: str) -> UsageReservationData | None:
            self.get_usage_reservation_calls += 1
            if self.get_usage_reservation_calls < 3:
                raise OperationalError("release usage reservation", {}, Exception("database is locked"))
            return await super().get_usage_reservation(reservation_id)

    repo = _BusyRepo()
    service = ApiKeysService(repo)
    monkeypatch.setattr("app.modules.api_keys.reservation_service.asyncio.sleep", _async_noop)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reservation-release-busy-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )
    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")

    await service.release_usage_reservation(reservation.reservation_id)

    stored = await repo.get_usage_reservation(reservation.reservation_id)
    limits = await repo.get_limits_by_key(created.id)
    assert stored is not None
    assert stored.status == "released"
    assert limits[0].current_value == 0
    assert repo.get_usage_reservation_calls == 4
    assert repo.rollback_calls >= 2


@pytest.mark.asyncio
async def test_fail_usage_reservation_preserves_failed_request_record() -> None:
    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="reservation-fail-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")
    await service.fail_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.1",
        input_tokens=None,
        output_tokens=None,
        cached_input_tokens=None,
    )

    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 0
    stored = await repo.get_usage_reservation(reservation.reservation_id)
    assert stored is not None
    assert stored.status == "failed"


@pytest.mark.asyncio
async def test_release_after_finalize_is_noop() -> None:

    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="finalize-then-release-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")
    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 100

    await service.finalize_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.1",
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=0,
    )

    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 15

    await service.release_usage_reservation(reservation.reservation_id)

    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 15


@pytest.mark.asyncio
async def test_finalize_after_release_is_noop() -> None:

    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    created = await service.create_key(
        ApiKeyCreateData(
            name="release-then-finalize-key",
            allowed_models=None,
            expires_at=None,
            limits=[
                LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=100),
            ],
        )
    )

    reservation = await service.enforce_limits_for_request(created.id, request_model="gpt-5.1")

    await service.release_usage_reservation(reservation.reservation_id)

    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 0

    await service.finalize_usage_reservation(
        reservation.reservation_id,
        model="gpt-5.1",
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=0,
    )

    limits = await repo.get_limits_by_key(created.id)
    assert limits[0].current_value == 0
