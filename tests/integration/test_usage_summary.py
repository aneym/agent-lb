from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.service import UsageService

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, provider: str = "openai") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        provider=provider,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_usage_summary_cost_includes_cached_tokens(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc1", "cached@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc1",
            request_id="req_summary_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=500,
            cached_input_tokens=200,
            reasoning_tokens=None,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )

        summary = await service.get_usage_summary()
        cost = summary.cost

        expected_raw = (800 / 1_000_000) * 1.25 + (200 / 1_000_000) * 0.125 + (500 / 1_000_000) * 10.0
        expected = round(expected_raw, 6)
        assert cost.total_usd_7d == pytest.approx(expected)


@pytest.mark.asyncio
async def test_usage_summary_metrics(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc2", "metrics@example.com"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc2",
            request_id="req_summary_2",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(hours=2),
        )
        await logs_repo.add_log(
            account_id="acc2",
            request_id="req_summary_3",
            model="gpt-5.1",
            input_tokens=5,
            output_tokens=0,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(hours=1),
        )

        summary = await service.get_usage_summary()
        metrics = summary.metrics
        assert metrics is not None
        assert metrics.requests_7d == 2
        assert metrics.tokens_secondary_window == 35
        assert metrics.error_rate_7d == pytest.approx(0.5)
        assert metrics.top_error == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_usage_summary_uses_persisted_request_log_cost(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc3", "persisted-cost@example.com"))

        now = utcnow()
        log = await logs_repo.add_log(
            account_id="acc3",
            request_id="req_summary_persisted_cost",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=500,
            cached_input_tokens=200,
            reasoning_tokens=None,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )
        await session.execute(update(log.__class__).where(log.__class__.id == log.id).values(cost_usd=9.876543))
        await session.commit()

        summary = await service.get_usage_summary()

        assert summary.cost.total_usd_7d == pytest.approx(9.876543)


@pytest.mark.asyncio
async def test_usage_summary_provider_filter_narrows_to_matching_accounts(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc_openai", "openai@example.com", provider="openai"))
        await accounts_repo.upsert(_make_account("acc_anthropic", "anthropic@example.com", provider="anthropic"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_openai",
            request_id="req_provider_openai",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )
        await logs_repo.add_log(
            account_id="acc_anthropic",
            request_id="req_provider_anthropic",
            model="claude-opus",
            input_tokens=200,
            output_tokens=100,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )

        summary = await service.get_usage_summary(provider="anthropic")

        assert summary.metrics is not None
        assert summary.metrics.requests_7d == 1
        assert summary.metrics.tokens_secondary_window == 300


@pytest.mark.asyncio
async def test_usage_summary_provider_filter_drops_unattributed_logs(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc_unattr", "unattr@example.com", provider="anthropic"))

        now = utcnow()
        await logs_repo.add_log(
            account_id=None,
            request_id="req_unattributed",
            model="unknown-model",
            input_tokens=10,
            output_tokens=5,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )

        unfiltered = await service.get_usage_summary()
        assert unfiltered.metrics is not None
        assert unfiltered.metrics.requests_7d == 1

        scoped = await service.get_usage_summary(provider="anthropic")
        assert scoped.metrics is not None
        assert scoped.metrics.requests_7d == 0


@pytest.mark.asyncio
async def test_usage_summary_unknown_provider_returns_empty_summary(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc_known", "known@example.com", provider="openai"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_known",
            request_id="req_known",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )

        summary = await service.get_usage_summary(provider="does-not-exist")

        assert summary.cost.total_usd_7d == 0
        assert summary.metrics is not None
        assert summary.metrics.requests_7d == 0


@pytest.mark.asyncio
async def test_usage_summary_no_provider_param_matches_unfiltered_behavior(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        service = UsageService(usage_repo, logs_repo, accounts_repo)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com", provider="openai"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com", provider="anthropic"))

        now = utcnow()
        await logs_repo.add_log(
            account_id="acc_a",
            request_id="req_no_provider_a",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )
        await logs_repo.add_log(
            account_id=None,
            request_id="req_no_provider_unattributed",
            model="unknown-model",
            input_tokens=10,
            output_tokens=5,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=5),
        )

        default_call = await service.get_usage_summary()
        explicit_none = await service.get_usage_summary(provider=None)

        assert default_call.metrics is not None and explicit_none.metrics is not None
        assert default_call.metrics.requests_7d == explicit_none.metrics.requests_7d == 2
        assert default_call == explicit_none
