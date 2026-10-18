from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.usage.repository import UsageRepository
from tests.integration.load_balancer_support import (
    _repo_factory,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_request_scoped_primary_quota_bypass_is_exact_and_not_persisted(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = Account(
        id="anthropic-request-scoped-bypass",
        provider="anthropic",
        email="anthropic-request-scoped-bypass@example.com",
        plan_type="max",
        access_token_encrypted=encryptor.encrypt("anthropic-access-request-scoped-bypass"),
        refresh_token_encrypted=encryptor.encrypt("anthropic-refresh-request-scoped-bypass"),
        last_refresh=now,
        status=AccountStatus.RATE_LIMITED,
        reset_at=now_epoch + 3600,
        blocked_at=now_epoch,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        await UsageRepository(session).add_entry(
            account_id=account.id,
            provider="anthropic",
            used_percent=100.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    balancer = LoadBalancer(_repo_factory)
    blocked = await balancer.select_account(
        provider="anthropic",
        account_ids=[account.id],
        routing_strategy="usage_weighted",
    )
    wrong_id = await balancer.select_account(
        provider="anthropic",
        account_ids=[account.id],
        ignore_primary_quota_account_ids=["another-account"],
        routing_strategy="usage_weighted",
    )
    bypassed = await balancer.select_account(
        provider="anthropic",
        account_ids=[account.id],
        ignore_primary_quota_account_ids=[account.id],
        routing_strategy="usage_weighted",
    )
    blocked_again = await balancer.select_account(
        provider="anthropic",
        account_ids=[account.id],
        routing_strategy="usage_weighted",
    )

    assert blocked.account is None
    assert wrong_id.account is None
    assert bypassed.account is not None
    assert bypassed.account.id == account.id
    assert blocked_again.account is None
    async with SessionLocal() as session:
        persisted = await session.get(Account, account.id)
        assert persisted is not None
        assert persisted.status == AccountStatus.RATE_LIMITED
        assert persisted.blocked_at == now_epoch


@pytest.mark.asyncio
async def test_request_scoped_primary_quota_bypass_secondary_exhaustion_is_advisory(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = Account(
        id="anthropic-primary-secondary-exhausted",
        provider="anthropic",
        email="anthropic-primary-secondary-exhausted@example.com",
        plan_type="max",
        access_token_encrypted=encryptor.encrypt("anthropic-access-primary-secondary-exhausted"),
        refresh_token_encrypted=encryptor.encrypt("anthropic-refresh-primary-secondary-exhausted"),
        last_refresh=now,
        status=AccountStatus.RATE_LIMITED,
        reset_at=now_epoch + 3600,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
        usage = UsageRepository(session)
        await usage.add_entry(
            account_id=account.id,
            provider="anthropic",
            used_percent=100.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage.add_entry(
            account_id=account.id,
            provider="anthropic",
            used_percent=100.0,
            window="secondary",
            reset_at=now_epoch + 7200,
            window_minutes=10080,
        )

    selection = await LoadBalancer(_repo_factory).select_account(
        provider="anthropic",
        account_ids=[account.id],
        ignore_primary_quota_account_ids=[account.id],
        routing_strategy="usage_weighted",
    )

    assert selection.account is not None
    assert selection.account.id == account.id
    async with SessionLocal() as session:
        persisted = await session.get(Account, account.id)
        assert persisted is not None
        assert persisted.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_load_balancer_skips_secondary_quota(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 3600
    secondary_reset = now_epoch + 7200

    account_a = Account(
        id="acc_secondary_full",
        email="secondary_full@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-a"),
        refresh_token_encrypted=encryptor.encrypt("refresh-a"),
        id_token_encrypted=encryptor.encrypt("id-a"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(
        id="acc_secondary_ok",
        email="secondary_ok@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-b"),
        refresh_token_encrypted=encryptor.encrypt("refresh-b"),
        id_token_encrypted=encryptor.encrypt("id-b"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(account_a)
        await accounts_repo.upsert(account_b)

        await usage_repo.add_entry(
            account_id=account_a.id,
            used_percent=10.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_a.id,
            used_percent=100.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=20.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=50.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account()

        assert selection.account is not None
        assert selection.account.id == account_b.id

        refreshed = await session.get(Account, account_a.id)
        assert refreshed is not None
        await session.refresh(refreshed)

        assert refreshed.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_load_balancer_reactivates_after_secondary_reset(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 3600
    secondary_reset = now_epoch + 7200

    account = Account(
        id="acc_secondary_reset",
        email="secondary_reset@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-reset"),
        refresh_token_encrypted=encryptor.encrypt("refresh-reset"),
        id_token_encrypted=encryptor.encrypt("id-reset"),
        last_refresh=now,
        status=AccountStatus.QUOTA_EXCEEDED,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(account)

        await usage_repo.add_entry(
            account_id=account.id,
            used_percent=5.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account.id,
            used_percent=0.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account()

        assert selection.account is not None
        assert selection.account.id == account.id

        refreshed = await session.get(Account, account.id)
        assert refreshed is not None
        await session.refresh(refreshed)
        assert refreshed.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_load_balancer_treats_weekly_only_primary_as_quota_window(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    weekly_reset = now_epoch + 7200
    plus_primary_reset = now_epoch + 3600
    plus_secondary_reset = now_epoch + 7200

    free_account = Account(
        id="acc_free_weekly_full",
        email="free_weekly_full@example.com",
        plan_type="free",
        access_token_encrypted=encryptor.encrypt("free-access"),
        refresh_token_encrypted=encryptor.encrypt("free-refresh"),
        id_token_encrypted=encryptor.encrypt("free-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    plus_account = Account(
        id="acc_plus_available",
        email="plus_available@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("plus-access"),
        refresh_token_encrypted=encryptor.encrypt("plus-refresh"),
        id_token_encrypted=encryptor.encrypt("plus-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(free_account)
        await accounts_repo.upsert(plus_account)

        await usage_repo.add_entry(
            account_id=free_account.id,
            used_percent=100.0,
            window="primary",
            reset_at=weekly_reset,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            account_id=plus_account.id,
            used_percent=20.0,
            window="primary",
            reset_at=plus_primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=plus_account.id,
            used_percent=20.0,
            window="secondary",
            reset_at=plus_secondary_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account()

        assert selection.account is not None
        assert selection.account.id == plus_account.id

        refreshed_free = await session.get(Account, free_account.id)
        assert refreshed_free is not None
        await session.refresh(refreshed_free)

        assert refreshed_free.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_load_balancer_prefers_newer_weekly_primary_over_stale_secondary(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    stale_reset = now_epoch + 1800
    weekly_reset = now_epoch + 7200
    plus_primary_reset = now_epoch + 3600
    plus_secondary_reset = now_epoch + 7200

    free_account = Account(
        id="acc_free_weekly_stale_secondary",
        email="free_weekly_stale_secondary@example.com",
        plan_type="free",
        access_token_encrypted=encryptor.encrypt("free-stale-access"),
        refresh_token_encrypted=encryptor.encrypt("free-stale-refresh"),
        id_token_encrypted=encryptor.encrypt("free-stale-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    plus_account = Account(
        id="acc_plus_weekly_control",
        email="plus_weekly_control@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("plus-control-access"),
        refresh_token_encrypted=encryptor.encrypt("plus-control-refresh"),
        id_token_encrypted=encryptor.encrypt("plus-control-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(free_account)
        await accounts_repo.upsert(plus_account)

        await usage_repo.add_entry(
            account_id=free_account.id,
            used_percent=15.0,
            window="secondary",
            reset_at=stale_reset,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            account_id=free_account.id,
            used_percent=100.0,
            window="primary",
            reset_at=weekly_reset,
            window_minutes=10080,
            recorded_at=now,
        )
        await usage_repo.add_entry(
            account_id=plus_account.id,
            used_percent=20.0,
            window="primary",
            reset_at=plus_primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=plus_account.id,
            used_percent=20.0,
            window="secondary",
            reset_at=plus_secondary_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account()

        assert selection.account is not None
        assert selection.account.id == plus_account.id

        refreshed_free = await session.get(Account, free_account.id)
        assert refreshed_free is not None
        await session.refresh(refreshed_free)

        assert refreshed_free.status == AccountStatus.ACTIVE
