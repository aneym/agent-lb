from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.auth.refresh import TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountRefreshClaim, AccountStatus, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module


@pytest.fixture(autouse=True)
def _clear_refresh_state() -> None:
    auth_manager_module._clear_refresh_singleflight_state()


def _rotated_result(account_id: str) -> TokenRefreshResult:
    return TokenRefreshResult(
        access_token="access-new",
        refresh_token="refresh-new",
        id_token="id-new",
        account_id=None,
        plan_type="plus",
        email=None,
    )


async def _create_account(account_id: str, *, refresh_token: str = "refresh-old") -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access-old"),
                refresh_token_encrypted=encryptor.encrypt(refresh_token),
                id_token_encrypted=encryptor.encrypt("id-old"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        session.add(
            StickySession(
                key=f"sticky-{account_id}",
                kind=StickySessionKind.STICKY_THREAD,
                account_id=account_id,
            )
        )
        await session.commit()


async def _insert_claim(account_id: str, *, claimed_by: str, expires_in_seconds: float) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            AccountRefreshClaim(
                account_id=account_id,
                claimed_by=claimed_by,
                claimed_at=now,
                claim_expires_at=now + timedelta(seconds=expires_in_seconds),
            )
        )
        await session.commit()


async def _account_snapshot(account_id: str) -> tuple[AccountStatus, str, bool]:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = (await session.execute(select(Account).where(Account.id == account_id))).scalars().one()
        sticky_present = (
            await session.execute(select(StickySession.key).where(StickySession.account_id == account_id))
        ).scalar_one_or_none() is not None
        return account.status, encryptor.decrypt(account.refresh_token_encrypted), sticky_present


async def _commit_terminal_status(account_id: str, *, status: AccountStatus, reason: str) -> None:
    """Simulate a prior claim holder committing a terminal status WITHOUT rotating
    the refresh token — a permanent ``invalid_grant`` downgrade or the
    safe-terminal persist-conflict path, both of which leave the consumed token
    stored."""
    async with SessionLocal() as session:
        account = (await session.execute(select(Account).where(Account.id == account_id))).scalars().one()
        account.status = status
        account.deactivation_reason = reason
        await session.commit()


async def _commit_peer_reauth(account_id: str, *, refresh_token: str) -> None:
    """Simulate a peer that genuinely re-authenticated: ROTATE the refresh token
    (fingerprint changes) and clear the terminal status back to ACTIVE."""
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = (await session.execute(select(Account).where(Account.id == account_id))).scalars().one()
        account.access_token_encrypted = encryptor.encrypt("access-new")
        account.refresh_token_encrypted = encryptor.encrypt(refresh_token)
        account.id_token_encrypted = encryptor.encrypt("id-new")
        account.status = AccountStatus.ACTIVE
        account.deactivation_reason = None
        await session.commit()


def _encode_jwt(payload: dict) -> str:
    import base64
    import json

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }
