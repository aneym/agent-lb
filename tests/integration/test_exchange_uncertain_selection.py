from __future__ import annotations

import base64
import json
import os
import time
from datetime import timedelta
from hashlib import sha256

import pytest
from sqlalchemy import select

from app.core.auth.refresh import RefreshError
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountExchangeIntent, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import _proxy_repo_context
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.load_balancer import LoadBalancer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "postgresql" not in os.environ.get("AGENT_LB_TEST_DATABASE_URL", ""), reason="requires Postgres"
    ),
]


def _jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_selection_preserves_uncertain_status_and_serves_unspent_access(db_setup, monkeypatch, provider: str):
    account_id = f"uncertain-selection-{provider}"
    encryptor = TokenEncryptor()
    refresh_token = f"refresh-{provider}"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            Account(
                id=account_id,
                provider=provider,
                email=f"{account_id}@example.invalid",
                plan_type="plus" if provider == "openai" else "claude",
                chatgpt_account_id=f"workspace-{account_id}" if provider == "openai" else None,
                access_token_encrypted=encryptor.encrypt(_jwt(time.time() + 360) if provider == "openai" else "access"),
                refresh_token_encrypted=encryptor.encrypt(refresh_token),
                id_token_encrypted=encryptor.encrypt("id") if provider == "openai" else None,
                last_refresh=utcnow() - timedelta(hours=2),
                access_expires_at=utcnow() + timedelta(minutes=6),
                status=AccountStatus.EXCHANGE_UNCERTAIN,
                deactivation_reason="Refresh exchange outcome uncertain",
            )
        )
        session.add(
            AccountExchangeIntent(
                account_id=account_id,
                refresh_token_sha256=sha256(refresh_token.encode()).hexdigest(),
                started_at=utcnow(),
                replay=False,
            )
        )
        await session.commit()

    calls = 0

    async def forbidden_refresh(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("uncertain exchange must not call the provider")

    monkeypatch.setattr(AuthManager, "_refresh_tokens", forbidden_refresh)
    balancer = LoadBalancer(_proxy_repo_context)
    for _ in range(3):
        selected = await balancer.select_account(provider=provider, account_ids=[account_id])
        assert selected.account is not None and selected.account.id == account_id
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            account = await repo.get_by_id(account_id)
            assert account is not None
            assert account.status == AccountStatus.EXCHANGE_UNCERTAIN
            served = await AuthManager(repo).ensure_fresh(account)
            assert served.status == AccountStatus.EXCHANGE_UNCERTAIN
    assert calls == 0
    async with SessionLocal() as session:
        assert (
            await session.execute(select(Account.status).where(Account.id == account_id))
        ).scalar_one() == AccountStatus.EXCHANGE_UNCERTAIN


@pytest.mark.asyncio
async def test_owned_uncertain_account_inside_margin_never_opens_intent_or_refreshes(db_setup, monkeypatch):
    account_id = "owned-uncertain-inside-margin"
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider="anthropic",
                email=f"{account_id}@example.invalid",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                last_refresh=utcnow() - timedelta(hours=2),
                access_expires_at=utcnow() + timedelta(minutes=4),
                status=AccountStatus.EXCHANGE_UNCERTAIN,
                deactivation_reason="Refresh exchange outcome uncertain",
            )
        )

    calls = 0

    async def forbidden_refresh(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("uncertain exchange must not call the provider")

    monkeypatch.setattr(AuthManager, "_refresh_tokens", forbidden_refresh)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        with pytest.raises(RefreshError) as error:
            await AuthManager(repo).ensure_fresh(account)
        assert error.value.code == "exchange_uncertain"
        assert account.status == AccountStatus.EXCHANGE_UNCERTAIN
        assert account.deactivation_reason == "Refresh exchange outcome uncertain"
        assert await repo.exchange_intent_hash(account_id) is None
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["mark_rate_limit", "mark_quota_exceeded", "mark_permanent_failure"])
async def test_balancer_marking_does_not_clear_concurrently_uncertain_exchange(db_setup, operation: str):
    account_id = f"uncertain-mark-{operation}"
    encryptor = TokenEncryptor()
    original_token = encryptor.encrypt("refresh")
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider="openai",
                email=f"{account_id}@example.invalid",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=original_token,
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        snapshot = await repo.get_by_id(account_id)
        assert snapshot is not None
        snapshot = Account(**{column.key: getattr(snapshot, column.key) for column in Account.__table__.columns})

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        assert await repo.begin_exchange(account_id, sha256(b"refresh").hexdigest(), original_token)
        await repo.mark_exchange_uncertain(account_id, sha256(b"refresh").hexdigest(), original_token)

    balancer = LoadBalancer(_proxy_repo_context)
    if operation == "mark_permanent_failure":
        await balancer.mark_permanent_failure(snapshot, "invalid_grant")
    else:
        await getattr(balancer, operation)(snapshot, {"message": "upstream unavailable"})
    async with SessionLocal() as session:
        stored = await AccountsRepository(session).get_by_id(account_id)
        assert stored is not None
        assert stored.status == AccountStatus.EXCHANGE_UNCERTAIN
        assert stored.deactivation_reason == "Refresh exchange outcome uncertain"
