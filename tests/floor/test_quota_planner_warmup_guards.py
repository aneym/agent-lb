from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.service_data import ApiKeyInvalidError, ApiKeyNotFoundError, ApiKeyRateLimitExceededError
from app.modules.quota_planner.logic import PlannerSettings
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.quota_planner.warmup import QuotaWarmupService, WarmupUsage

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_quota_planner_warm_now_defaults_to_safe_skip(async_client, db_setup):
    del db_setup

    response = await async_client.post(
        "/api/quota-planner/warm-now",
        json={"accountId": "acc-missing", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    assert payload["reason"] == "account_not_found"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_does_not_execute_canceled_decision(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-canceled-warm",
            email="canceled-warm@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        repo = QuotaPlannerRepository(session)
        await repo.upsert_settings(
            PlannerSettings(
                mode="auto",
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
            )
        )
        decision = await repo.log_decision(
            mode="auto",
            action="warmup",
            idempotency_key="cancelled-auto-warmup",
            account_id=account.id,
            scheduled_at=utcnow(),
            score=3.0,
            reason="operator_review",
            status="planned",
        )
        await repo.update_decision_status(decision.id, status="canceled", reason="admin_canceled")

        async def fail_send(*args, **kwargs):
            del args, kwargs
            raise AssertionError("canceled decision should not execute")

        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await QuotaWarmupService(session).warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            decision_id=decision.id,
        )

    assert result.status == "canceled"
    assert result.reason == "admin_canceled"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_executes_when_explicitly_gated(monkeypatch, async_client, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm",
            email="warm@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        repo = QuotaPlannerRepository(session)
        await repo.upsert_settings(
            PlannerSettings(
                mode="auto",
                timezone="UTC",
                working_days=(0, 1, 2, 3, 4),
                working_hours_start="09:00",
                working_hours_end="18:00",
                prewarm_enabled=True,
                prewarm_lead_minutes=300,
                max_warmups_per_day=3,
                max_warmup_credits_per_day=1.0,
                min_expected_gain=1.0,
                forecast_quantile="p75",
                allow_synthetic_traffic=True,
                warmup_model_preference="gpt-5.4-mini",
                dry_run=False,
            )
        )
        await repo.add_window_observation(
            account_id="acc-warm",
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )

    async def fake_send(self, *, account, model, request_id):
        del self, account, model, request_id
        return WarmupUsage(input_tokens=3, output_tokens=1, cached_input_tokens=0, reasoning_tokens=None)

    async def failing_record_effect(self, account, model, *, source, confidence):
        del self, account, model, source, confidence
        raise RuntimeError("usage refresh unavailable")

    monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fake_send)
    monkeypatch.setattr(QuotaWarmupService, "_record_warmup_effect", failing_record_effect)

    response = await async_client.post(
        "/api/quota-planner/warm-now",
        json={"accountId": "acc-warm", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "executed"
    async with SessionLocal() as session:
        logs = await session.execute(select(RequestLog).where(RequestLog.request_kind == "warmup"))
        row = logs.scalar_one()
        assert row.request_id == payload["requestId"]
        assert (row.caller_user_source, row.caller_machine_source) == ("internal", "internal")


@pytest.mark.asyncio
async def test_quota_planner_warm_now_cancellation_releases_api_key_reservation(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-cancel-reservation",
            email="warm-cancel-reservation@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        repo = QuotaPlannerRepository(session)
        await repo.upsert_settings(
            PlannerSettings(
                mode="auto",
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)
        failed_reservations: list[tuple[str, str, int | None, int | None, int | None]] = []

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                return SimpleNamespace(reservation_id="reservation-cancelled")

            async def fail_usage_reservation(
                self,
                reservation_id,
                *,
                model,
                input_tokens=None,
                output_tokens=None,
                cached_input_tokens=None,
            ):
                failed_reservations.append((reservation_id, model, input_tokens, output_tokens, cached_input_tokens))

        async def cancel_probe(self, *, account, model, request_id):
            del self, account, model, request_id
            raise asyncio.CancelledError()

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", cancel_probe)

        with pytest.raises(asyncio.CancelledError):
            await service.warm_now(
                account_id=account.id,
                model="gpt-5.4-mini",
                api_key_id="api-key-cancel",
                force_probe=True,
            )

    assert failed_reservations == [("reservation-cancelled", "gpt-5.4-mini", 0, 0, 0)]


@pytest.mark.asyncio
async def test_quota_planner_warm_now_api_key_not_found_is_skipped(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-key-not-found",
            email="warm-key-not-found@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        repo = QuotaPlannerRepository(session)
        await repo.upsert_settings(
            PlannerSettings(
                mode="auto",
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                raise ApiKeyNotFoundError("API key not found: not-existing")

        async def fail_send(self, *, account, model, request_id):
            del self, account, model, request_id
            raise AssertionError("invalid API key should skip before sending warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-not-found",
            force_probe=True,
        )

    assert result.status == "skipped"
    assert result.reason == "api_key_not_found"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_invalid_api_key_is_skipped(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-key-invalid",
            email="warm-key-invalid@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        repo = QuotaPlannerRepository(session)
        await repo.upsert_settings(
            PlannerSettings(
                mode="auto",
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                raise ApiKeyInvalidError("API key has expired")

        async def fail_send(self, *, account, model, request_id):
            del self, account, model, request_id
            raise AssertionError("expired API key should skip before sending warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-expired",
            force_probe=True,
        )

    assert result.status == "skipped"
    assert result.reason == "api_key_invalid"


@pytest.mark.asyncio
async def test_quota_planner_warm_now_rate_limited_api_key_is_skipped(monkeypatch, db_setup):
    del db_setup
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="acc-warm-key-rate-limited",
            email="warm-key-rate-limited@example.test",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        repo = QuotaPlannerRepository(session)
        await repo.upsert_settings(
            PlannerSettings(
                mode="auto",
                allow_synthetic_traffic=True,
                dry_run=False,
                max_warmup_credits_per_day=1.0,
                warmup_model_preference="gpt-5.4-mini",
            )
        )
        await repo.add_window_observation(
            account_id=account.id,
            model="gpt-5.4-mini",
            source="warmup_probe",
            confidence="observed",
        )
        service = QuotaWarmupService(session)

        class FakeApiKeys:
            async def enforce_limits_for_request(self, *args, **kwargs):
                del args, kwargs
                raise ApiKeyRateLimitExceededError(message="Too many requests", reset_at=utcnow())

        async def fail_send(self, *, account, model, request_id):
            del self, account, model, request_id
            raise AssertionError("rate-limited API key should skip before sending warmup probe")

        monkeypatch.setattr(service, "_api_keys", FakeApiKeys())
        monkeypatch.setattr(QuotaWarmupService, "_send_warmup_probe", fail_send)

        result = await service.warm_now(
            account_id=account.id,
            model="gpt-5.4-mini",
            api_key_id="api-key-rate-limited",
            force_probe=True,
        )

    assert result.status == "skipped"
    assert result.reason.startswith("api_key_rate_limit_exceeded:")
