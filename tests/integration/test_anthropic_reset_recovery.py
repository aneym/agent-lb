from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, update

from app.core.clients import anthropic_resets
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ResetCreditAttempt
from app.db.session import SessionLocal
from app.modules.accounts import api as accounts_api
from app.modules.accounts import service as service_module
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.reset_credit_attempts import ResetCreditAttemptsRepository
from app.modules.accounts.service import AccountResetCreditsUnavailableError, AccountsService
from app.modules.usage.repository import UsageRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def seed():
    enc = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="claude-reset",
                email="reset@example.invalid",
                provider="anthropic",
                plan_type="claude",
                status=AccountStatus.QUOTA_EXCEEDED,
                access_token_encrypted=enc.encrypt("test-access"),
                refresh_token_encrypted=enc.encrypt("test-refresh"),
                id_token_encrypted=enc.encrypt("test-id"),
                last_refresh=utcnow(),
            )
        )
        await session.commit()


def service(session):
    result = AccountsService(AccountsRepository(session), UsageRepository(session))
    result._usage_updater = AsyncMock()
    return result


@pytest.fixture
def reset_upstream(monkeypatch):
    inventory = anthropic_resets.ResetStatus.model_validate(
        {
            "eligible": True,
            "at_limit": True,
            "exhausted": ["seven_day"],
            "next_grant_id": "launch",
            "grants": [{"id": "launch", "resets_left": 1, "usable_now": True, "clears": ["seven_day"]}],
        }
    )
    fetch = AsyncMock(return_value=inventory)
    redeem = AsyncMock(return_value=anthropic_resets.ResetResult(result="reset", cleared=["seven_day"]))
    monkeypatch.setattr(anthropic_resets, "fetch_status", fetch)
    monkeypatch.setattr(anthropic_resets, "redeem", redeem)
    monkeypatch.setattr(service_module, "refresh_standard_capacity", AsyncMock(return_value=True))
    return fetch, redeem


async def expire():
    async with SessionLocal() as session:
        await session.execute(update(ResetCreditAttempt).values(lease_until=utcnow() - timedelta(seconds=1)))
        await session.commit()


async def test_claude_unknown_redemption_never_spends_a_second_credit(db_setup, reset_upstream):
    await seed()
    _, redeem = reset_upstream
    redeem.side_effect = TimeoutError("response lost")
    async with SessionLocal() as session:
        with pytest.raises(TimeoutError):
            await service(session).redeem_rate_limit_reset_credit("claude-reset")
    await expire()
    async with SessionLocal() as session:
        with pytest.raises(AccountResetCreditsUnavailableError, match="unresolved"):
            await service(session).redeem_rate_limit_reset_credit("claude-reset", override_daily_limit=True)
        assert (await ResetCreditAttemptsRepository(session).active()).state == "pending"
    assert redeem.await_count == 1


async def test_claude_reset_survives_refresh_failure_without_second_redemption(db_setup, reset_upstream, monkeypatch):
    await seed()
    _, redeem = reset_upstream
    monkeypatch.setattr(service_module, "refresh_standard_capacity", AsyncMock(side_effect=RuntimeError("refresh")))
    async with SessionLocal() as session:
        with pytest.raises(RuntimeError, match="refresh"):
            await service(session).redeem_rate_limit_reset_credit("claude-reset")
    await expire()
    monkeypatch.setattr(service_module, "refresh_standard_capacity", AsyncMock(return_value=True))
    async with SessionLocal() as session:
        result = await service(session).redeem_rate_limit_reset_credit("claude-reset")
        assert result.status == "redeemed"
        assert await ResetCreditAttemptsRepository(session).active() is None
    assert redeem.await_count == 1


async def test_claude_daily_reset_cap_includes_manual_redemptions(db_setup, reset_upstream):
    await seed()
    _, redeem = reset_upstream
    async with SessionLocal() as session:
        assert (await service(session).redeem_rate_limit_reset_credit("claude-reset")).status == "redeemed"
        with pytest.raises(AccountResetCreditsUnavailableError, match="last 24 hours"):
            await service(session).redeem_rate_limit_reset_credit("claude-reset")
    assert redeem.await_count == 1


async def test_manual_override_records_trigger_and_spends_after_daily_cap(db_setup, reset_upstream):
    await seed()
    _, redeem = reset_upstream
    async with SessionLocal() as session:
        assert (await service(session).redeem_rate_limit_reset_credit("claude-reset")).status == "redeemed"
        result = await service(session).redeem_rate_limit_reset_credit("claude-reset", override_daily_limit=True)
        assert result.status == "redeemed"
        rows = await session.execute(select(ResetCreditAttempt.trigger).order_by(ResetCreditAttempt.created_at))
        triggers = rows.scalars().all()
    assert triggers == ["manual", "manual_override"]
    assert redeem.await_count == 2


async def test_auto_cannot_request_override_and_remains_capped(db_setup, reset_upstream):
    await seed()
    _, redeem = reset_upstream
    async with SessionLocal() as session:
        assert (await service(session).redeem_rate_limit_reset_credit("claude-reset")).status == "redeemed"
        with pytest.raises(AccountResetCreditsUnavailableError, match="Only manual"):
            await service(session).redeem_rate_limit_reset_credit(
                "claude-reset", trigger="auto", override_daily_limit=True
            )
        with pytest.raises(AccountResetCreditsUnavailableError, match="last 24 hours"):
            await service(session).redeem_rate_limit_reset_credit("claude-reset", trigger="auto")
    assert redeem.await_count == 1


async def test_manual_override_does_not_spend_ineligible_grant(db_setup, reset_upstream):
    await seed()
    fetch, redeem = reset_upstream
    fetch.return_value = anthropic_resets.ResetStatus.model_validate(
        {"eligible": True, "at_limit": False, "grants": [{"id": "launch", "resets_left": 1, "usable_now": True}]}
    )
    async with SessionLocal() as session:
        result = await service(session).redeem_rate_limit_reset_credit("claude-reset", override_daily_limit=True)
        attempts = (await session.execute(select(ResetCreditAttempt))).scalars().all()
    assert result.code == "not_eligible"
    assert attempts == []
    redeem.assert_not_awaited()


async def test_claude_inventory_distinguishes_banked_credit_from_usable_reset(db_setup, reset_upstream):
    await seed()
    fetch, _ = reset_upstream
    fetch.return_value = anthropic_resets.ResetStatus.model_validate(
        {
            "eligible": True,
            "at_limit": False,
            "next_grant_id": "launch",
            "grants": [{"id": "launch", "resets_left": 1, "usable_now": False}],
        }
    )
    async with SessionLocal() as session:
        inventory = await service(session).list_rate_limit_reset_credits("claude-reset")
    assert inventory is not None
    assert inventory.available_count == 1
    assert inventory.redeemable_now is False
    assert inventory.ineligible_reason == "not_at_limit"


async def test_provider_early_use_grant_redeems_before_limit(db_setup, reset_upstream):
    await seed()
    fetch, redeem = reset_upstream
    fetch.return_value = anthropic_resets.ResetStatus.model_validate(
        {
            "eligible": True,
            "at_limit": False,
            "exhausted": [],
            "next_grant_id": "launch",
            "grants": [
                {"id": "launch", "resets_left": 1, "usable_now": True, "use_requires_limit": False}
            ],
        }
    )
    async with SessionLocal() as session:
        inventory = await service(session).list_rate_limit_reset_credits("claude-reset")
        result = await service(session).redeem_rate_limit_reset_credit("claude-reset")
    assert inventory is not None and inventory.redeemable_now is True
    assert result.status == "redeemed"
    redeem.assert_awaited_once()


async def test_claude_consume_api_requires_explicit_override_and_audits_it(async_client, reset_upstream, monkeypatch):
    await seed()
    _, redeem = reset_upstream
    audit = Mock()
    monkeypatch.setattr(accounts_api.AuditService, "log_async", audit)
    path = "/api/accounts/claude-reset/rate-limit-reset-credits/consume"

    first = await async_client.post(path)
    blocked = await async_client.post(path)
    overridden = await async_client.post(path, json={"overrideDailyLimit": True})

    assert first.status_code == 200, first.text
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "account_reset_credits_unavailable"
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["code"] == "reset"
    assert redeem.await_count == 2
    assert [call.kwargs["details"]["override_daily_limit"] for call in audit.call_args_list] == [False, True]


async def test_claude_already_used_is_not_reported_as_our_success(db_setup, reset_upstream):
    await seed()
    _, redeem = reset_upstream
    redeem.return_value = anthropic_resets.ResetResult(result="already_used")
    async with SessionLocal() as session:
        result = await service(session).redeem_rate_limit_reset_credit("claude-reset")
        assert result.status == "not_redeemed"
        assert result.code == "already_used"


async def test_failed_claude_inventory_clears_previous_known_count(db_setup, reset_upstream):
    from app.core.clients.rate_limit_resets import ResetCreditsError
    from app.modules.accounts import reset_credit_cache

    await seed()
    fetch, _ = reset_upstream
    reset_credit_cache.record_count("claude-reset", 2)
    fetch.side_effect = ResetCreditsError(502, "malformed timestamp")
    async with SessionLocal() as session:
        with pytest.raises(ResetCreditsError):
            await service(session).list_rate_limit_reset_credits("claude-reset")
    assert reset_credit_cache.get_count("claude-reset") is None
