from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.clients import anthropic_resets, rate_limit_resets
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.accounts.service import AccountResetCreditsUnavailableError, AccountsService

pytestmark = pytest.mark.unit


def _account(provider: str, owner: str | None) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=f"{provider}-credit",
        provider=provider,
        email=f"{provider}@example.invalid",
        plan_type="pro",
        status=AccountStatus.QUOTA_EXCEEDED,
        owner_instance=owner,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        last_refresh=datetime(2026, 9, 26),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_manual_reset_refuses_mirror_before_provider_call(monkeypatch, provider: str):
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    account = _account(provider, "forge")
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=account))
    service = AccountsService(repo)
    codex = AsyncMock()
    claude = AsyncMock()
    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", codex)
    monkeypatch.setattr(anthropic_resets, "fetch_status", claude)
    try:
        with pytest.raises(AccountResetCreditsUnavailableError, match="not locally owned"):
            await service.redeem_rate_limit_reset_credit(account.id)
        codex.assert_not_awaited()
        claude.assert_not_awaited()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_locally_owned_reset_reaches_provider(monkeypatch):
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    account = _account("anthropic", "studio")
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=account), session=object())
    service = AccountsService(repo)
    service._reset_attempts = SimpleNamespace(active=AsyncMock(return_value=None))
    provider = AsyncMock(return_value=SimpleNamespace(usable_grant=lambda now, credit_id: None))
    monkeypatch.setattr(anthropic_resets, "fetch_status", provider)
    try:
        result = await service.redeem_rate_limit_reset_credit(account.id)
        assert result is not None and result.status == "not_redeemed"
        provider.assert_awaited_once()
    finally:
        get_settings.cache_clear()
