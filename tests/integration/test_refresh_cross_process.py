"""Two agent-lb processes must never present the same OpenAI refresh token.

lb-restart runs a standby and a draining primary against one database. Each
process has its own refresh singleflight, so without a database-level lock both
can read the same refresh token and both call the token endpoint. OpenAI refresh
tokens are single use: the second call gets refresh_token_reused and the account
is marked reauth_required, which needs a human re-login.

Each AuthManager here stands for one process: its own session, and
refresh_account called directly (the in-process singleflight sits above it).
The fake token endpoint rotates like OpenAI's: a token works once.

Needs Postgres (the lock is a Postgres advisory lock):
AGENT_LB_TEST_DATABASE_URL=postgresql+asyncpg://.../<scratch db> pytest tests/integration/test_refresh_cross_process.py
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "postgresql" not in os.environ.get("AGENT_LB_TEST_DATABASE_URL", ""),
        reason="cross-process refresh lock is a Postgres advisory lock",
    ),
]


class _RotatingTokenEndpoint:
    """OpenAI-style refresh: each refresh token is accepted once, then rejected as reused."""

    def __init__(self) -> None:
        self.presented: list[str] = []
        self._spent: set[str] = set()

    async def refresh(self, refresh_token: str) -> TokenRefreshResult:
        self.presented.append(refresh_token)
        if refresh_token in self._spent:
            raise RefreshError("refresh_token_reused", "Refresh token was reused", True)
        self._spent.add(refresh_token)
        await asyncio.sleep(0.5)  # network time: long enough for a second process to overlap
        n = len(self._spent)
        return TokenRefreshResult(
            access_token=f"access-{n}",
            refresh_token=f"refresh-{n}",
            id_token=f"id-{n}",
            account_id=None,
            plan_type=None,
            email=None,
        )


@pytest.mark.asyncio
async def test_two_processes_refreshing_one_account_present_the_token_once(db_setup, monkeypatch):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="acc-refresh-race",
                email="race@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access-0"),
                refresh_token_encrypted=encryptor.encrypt("refresh-0"),
                id_token_encrypted=encryptor.encrypt("id-0"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )

    endpoint = _RotatingTokenEndpoint()

    async def _refresh_tokens(self: AuthManager, refresh_token: str, **_: object) -> TokenRefreshResult:
        return await endpoint.refresh(refresh_token)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", _refresh_tokens)

    async def one_process() -> Account:
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            account = await repo.get_by_id("acc-refresh-race")
            assert account is not None
            return await AuthManager(repo).refresh_account(account)

    first, second = await asyncio.gather(one_process(), one_process())

    assert endpoint.presented == ["refresh-0"]
    assert encryptor.decrypt(first.refresh_token_encrypted) == "refresh-1"
    assert encryptor.decrypt(second.refresh_token_encrypted) == "refresh-1"
    async with SessionLocal() as session:
        stored = await AccountsRepository(session).get_by_id("acc-refresh-race")
    assert stored is not None
    assert stored.status == AccountStatus.ACTIVE
    assert encryptor.decrypt(stored.refresh_token_encrypted) == "refresh-1"
