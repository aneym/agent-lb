from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.clients.oauth import OAuthTokens
from app.core.crypto import TokenEncryptor
from app.db.models import Account
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth.schemas import OauthStartRequest
from app.modules.oauth.service import OauthService
from app.modules.oauth.state import OAuthStateStore
from tests.floor.oauth_flow_support import _encode_jwt, _oauth_state_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _tokens(seat: str = "seat-1", *, access: str = "new-access") -> OAuthTokens:
    return OAuthTokens(
        access_token=access,
        refresh_token="new-refresh",
        id_token=_encode_jwt(
            {
                "sub": seat,
                "email": "same@example.invalid",
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "workspace-account",
                    "chatgpt_user_id": seat,
                    "workspace_label": "workspace-label",
                    "chatgpt_plan_type": "plus",
                },
            }
        ),
    )


async def _start(service: OauthService, monkeypatch, *, provider="openai", account_id=None):
    monkeypatch.setattr(service, "_ensure_callback_server", AsyncMock())
    started = await service.start_oauth(
        OauthStartRequest(force_method="browser", provider=provider, account_id=account_id)
    )
    assert started.authorization_url is not None
    return started, _oauth_state_token(started.authorization_url)


async def test_restart_keeps_provider_and_intended_account_and_success_is_sticky(db_setup, monkeypatch):
    async with SessionLocal() as session:
        first = OauthService(AccountsRepository(session), store=OAuthStateStore())
        started, state = await _start(first, monkeypatch, provider="anthropic", account_id="target")
        restarted = OauthService(AccountsRepository(session), store=OAuthStateStore())
        exchange = AsyncMock(return_value=OAuthTokens(access_token="a", refresh_token="r"))
        persist = AsyncMock()
        monkeypatch.setattr(restarted, "_exchange_authorization_code", exchange)
        monkeypatch.setattr(restarted, "_persist_tokens", persist)
        response = await restarted.manual_callback(f"code#{state}", started.flow_id)
        assert response.status == "success"
        assert exchange.await_args.args == ("anthropic",)
        assert persist.await_args.kwargs == {"provider_name": "anthropic", "intended_account_id": "target"}
        stale_error = await first.manual_callback(
            f"http://localhost/?error=access_denied&state={state}", started.flow_id
        )
        assert stale_error.status == "success"
        assert (await first.oauth_status(started.flow_id)).status == "success"
        assert persist.await_count == 1


async def test_targeted_openai_reauth_rejects_another_seat_and_preserves_identity(db_setup, monkeypatch):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        service = OauthService(repo, store=OAuthStateStore())
        exchange = AsyncMock(return_value=_tokens())
        monkeypatch.setattr(service, "_exchange_authorization_code", exchange)
        started, state = await _start(service, monkeypatch)
        assert (
            await service.manual_callback(f"http://localhost/?code=c&state={state}", started.flow_id)
        ).status == "success"
        original = (await session.execute(select(Account))).scalars().one()
        assert original.chatgpt_user_id == "seat-1"
        assert original.workspace_label == "workspace-label"
        original_token = original.access_token_encrypted
        original_id = original.id
        targeted, state = await _start(service, monkeypatch, account_id=original_id)
        exchange.return_value = _tokens("seat-2", access="wrong-seat")
        response = await service.manual_callback(f"http://localhost/?code=c&state={state}", targeted.flow_id)
        assert response.status == "error"
        await session.refresh(original)
        assert original.access_token_encrypted == original_token
        correct, state = await _start(service, monkeypatch, account_id=original_id)
        exchange.return_value = _tokens("seat-1", access="correct-seat")
        assert (
            await service.manual_callback(f"http://localhost/?code=c&state={state}", correct.flow_id)
        ).status == "success"
        await session.refresh(original)
        assert TokenEncryptor().decrypt(original.access_token_encrypted) == "correct-seat"
        assert original.id == original_id
        assert len((await session.execute(select(Account))).scalars().all()) == 1


async def test_targeted_anthropic_reauth_uses_its_provider_identity(db_setup, monkeypatch):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        service = OauthService(repo, store=OAuthStateStore())
        exchange = AsyncMock(
            return_value=OAuthTokens(
                access_token="a", refresh_token="r", account_id="anthropic-seat", email="a@example.invalid"
            )
        )
        monkeypatch.setattr(service, "_exchange_authorization_code", exchange)
        started, state = await _start(service, monkeypatch, provider="anthropic")
        assert (await service.manual_callback(f"code#{state}", started.flow_id)).status == "success"
        original = (await session.execute(select(Account))).scalars().one()
        targeted, state = await _start(service, monkeypatch, provider="anthropic", account_id=original.id)
        exchange.return_value = OAuthTokens(
            access_token="rotated", refresh_token="r2", account_id="anthropic-seat", email="a@example.invalid"
        )
        assert (await service.manual_callback(f"code#{state}", targeted.flow_id)).status == "success"
        await session.refresh(original)
        assert original.provider == "anthropic"
        assert TokenEncryptor().decrypt(original.access_token_encrypted) == "rotated"
        assert len((await session.execute(select(Account))).scalars().all()) == 1
