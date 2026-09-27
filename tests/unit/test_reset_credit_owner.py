from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.reset_credit_scheduler import ResetCreditAutoRedeemScheduler, exhausted_pool_or_none

pytestmark = pytest.mark.unit


def _account(account_id: str, owner: str | None) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.invalid",
        plan_type="plus",
        status=AccountStatus.QUOTA_EXCEEDED,
        owner_instance=owner,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        last_refresh=datetime(2026, 9, 26),
    )


def test_exhausted_pool_skips_mirrored_credit_candidates(monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    mirrored = _account("mirror", "forge")
    owned = _account("owned", "studio")
    assert [account.id for account in exhausted_pool_or_none([mirrored, owned])] == ["owned"]
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pending_recovery_does_not_redeem_mirror(monkeypatch):
    from app.core.config.settings import get_settings
    from app.modules.accounts import reset_credit_scheduler as scheduler_module

    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    scheduler = ResetCreditAutoRedeemScheduler(interval_seconds=60, cooldown_seconds=60, enabled=True)
    leader = AsyncMock()
    leader.try_acquire.return_value = True
    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: leader)
    attempt = AsyncMock()
    attempt.account_id = "mirror"
    ledger = AsyncMock()
    ledger.active.return_value = attempt
    monkeypatch.setattr(scheduler_module, "ResetCreditAttemptsRepository", lambda session: ledger)
    repo = AsyncMock()
    repo.get_by_id.return_value = _account("mirror", "forge")
    monkeypatch.setattr(scheduler_module, "AccountsRepository", lambda session: repo)
    service = AsyncMock()
    monkeypatch.setattr(scheduler_module, "_build_accounts_service", lambda repo, session: service)

    class _Context:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(scheduler_module, "get_background_session", lambda: _Context())
    await scheduler._tick()
    service.redeem_rate_limit_reset_credit.assert_not_awaited()
    get_settings.cache_clear()
