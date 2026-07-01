from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import AccountStatus
from app.modules.accounts.schemas import AccountSubscriptionLedger
from app.modules.accounts.service import AccountsService, AccountStateTransitionError

pytestmark = pytest.mark.unit

_ACCOUNT_ID = "acc_state_transition"


def _account(
    status: AccountStatus,
    *,
    deactivation_reason: str | None = None,
    reset_at: int | None = None,
    blocked_at: int | None = None,
) -> Any:
    return SimpleNamespace(
        status=status,
        deactivation_reason=deactivation_reason,
        reset_at=reset_at,
        blocked_at=blocked_at,
    )


def _subscription_account(*, provider: str = "anthropic") -> Any:
    encryptor = TokenEncryptor()
    return SimpleNamespace(
        id=_ACCOUNT_ID,
        provider=provider,
        chatgpt_account_id="workspace-subscription-check",
        access_token_encrypted=encryptor.encrypt("access-token"),
        subscription_next_charge_at=None,
        subscription_current_period_end_at=datetime(2026, 6, 22, 4, 0),
        subscription_amount=200.0,
        subscription_currency="USD",
        subscription_status="canceled",
    )


@pytest.mark.asyncio
async def test_pause_account_uses_conditional_status_update() -> None:
    account = _account(AccountStatus.ACTIVE)
    repo = AsyncMock()
    repo.get_by_id.return_value = account
    repo.update_status_if_current.return_value = True
    service = AccountsService(repo=repo)

    result = await service.pause_account(_ACCOUNT_ID)

    assert result is True
    repo.update_status_if_current.assert_awaited_once_with(
        _ACCOUNT_ID,
        AccountStatus.PAUSED,
        None,
        None,
        blocked_at=None,
        expected_status=AccountStatus.ACTIVE,
        expected_deactivation_reason=None,
        expected_reset_at=None,
        expected_blocked_at=None,
    )
    repo.update_status.assert_not_called()


@pytest.mark.asyncio
async def test_pause_account_raises_when_conditional_update_misses() -> None:
    repo = AsyncMock()
    repo.get_by_id.return_value = _account(AccountStatus.ACTIVE)
    repo.update_status_if_current.return_value = False
    service = AccountsService(repo=repo)

    with pytest.raises(AccountStateTransitionError, match="state changed"):
        await service.pause_account(_ACCOUNT_ID)


@pytest.mark.asyncio
async def test_reactivate_account_rejects_reauth_required_account() -> None:
    repo = AsyncMock()
    repo.get_by_id.return_value = _account(
        AccountStatus.REAUTH_REQUIRED,
        deactivation_reason="Authentication token invalidated - re-login required",
    )
    service = AccountsService(repo=repo)

    with pytest.raises(AccountStateTransitionError, match="requires re-authentication"):
        await service.reactivate_account(_ACCOUNT_ID)

    repo.update_status_if_current.assert_not_called()


@pytest.mark.asyncio
async def test_subscription_ledger_normalizes_aware_datetimes_for_storage() -> None:
    repo = AsyncMock()
    repo.update_subscription_ledger.return_value = True
    service = AccountsService(repo=repo)
    current_period_end_at = datetime(2026, 6, 22, 4, 0, tzinfo=timezone.utc)
    last_verified_at = datetime(2026, 6, 13, 15, 30, tzinfo=timezone.utc)

    result = await service.set_subscription_ledger(
        _ACCOUNT_ID,
        AccountSubscriptionLedger(
            status="paused",
            current_period_end_at=current_period_end_at,
            last_verified_at=last_verified_at,
            notes="  Paused in vendor billing UI.  ",
        ),
    )

    repo.update_subscription_ledger.assert_awaited_once_with(
        _ACCOUNT_ID,
        status="paused",
        next_charge_at=None,
        current_period_end_at=current_period_end_at.replace(tzinfo=None),
        amount=None,
        currency=None,
        last_verified_at=last_verified_at.replace(tzinfo=None),
        notes="Paused in vendor billing UI.",
    )
    assert result is not None
    assert result.current_period_end_at == current_period_end_at.replace(tzinfo=None)
    assert result.last_verified_at == last_verified_at.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_check_subscription_marks_working_account_active(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock()
    repo.get_by_id.return_value = _subscription_account()
    repo.update_subscription_ledger.return_value = True
    service = AccountsService(repo=repo)

    async def _fake_check(**_: object) -> tuple[int, str | None]:
        return 200, None

    monkeypatch.setattr(service, "_send_anthropic_subscription_check_request", _fake_check)

    result = await service.check_subscription(_ACCOUNT_ID)

    assert result is not None
    assert result.working is True
    assert result.probe_status_code == 200
    assert result.subscription is not None
    assert result.subscription.status == "active"
    repo.update_subscription_ledger.assert_awaited_once()
    assert repo.update_subscription_ledger.await_args.kwargs["status"] == "active"
    assert repo.update_subscription_ledger.await_args.kwargs["amount"] == 200.0


@pytest.mark.asyncio
async def test_check_subscription_keeps_failed_account_canceled(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock()
    repo.get_by_id.return_value = _subscription_account()
    repo.update_subscription_ledger.return_value = True
    service = AccountsService(repo=repo)

    async def _fake_check(**_: object) -> tuple[int, str | None]:
        return 403, "OAuth authentication is currently not allowed for this organization."

    monkeypatch.setattr(service, "_send_anthropic_subscription_check_request", _fake_check)

    result = await service.check_subscription(_ACCOUNT_ID)

    assert result is not None
    assert result.working is False
    assert result.probe_status_code == 403
    assert result.subscription is not None
    assert result.subscription.status == "canceled"
    assert "OAuth authentication is currently not allowed" in (result.subscription.notes or "")
    repo.update_subscription_ledger.assert_awaited_once()
    assert repo.update_subscription_ledger.await_args.kwargs["status"] == "canceled"


@pytest.mark.asyncio
async def test_check_subscription_rejects_non_canceled_account(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock()
    repo.get_by_id.return_value = _subscription_account()
    repo.get_by_id.return_value.subscription_status = "active"
    service = AccountsService(repo=repo)

    async def _fake_check(**_: object) -> tuple[int, str | None]:
        raise AssertionError("non-canceled subscriptions should not be probed")

    monkeypatch.setattr(service, "_send_anthropic_subscription_check_request", _fake_check)

    with pytest.raises(AccountStateTransitionError, match="only available for canceled accounts"):
        await service.check_subscription(_ACCOUNT_ID)

    repo.update_subscription_ledger.assert_not_awaited()
