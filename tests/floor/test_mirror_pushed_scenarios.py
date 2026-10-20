"""Box nodes see the accounts other nodes push to Studio (p6, 2026-10-01 17:57 ET).

Studio's mirror exported only the accounts it owns. The box nodes (ax42, pc-wsl, forge) mirror
Studio, so they never saw the accounts Nate's node pushes in (25600cac, 86fe250c) and piled all
box traffic onto two Studio-owned accounts until their 5-hour windows ran out. With
AGENT_LB_FEDERATION_MIRROR_INCLUDE_PUSHED on, the mirror also carries pushed-in accounts: access
token only, never a refresh token, so the pushing node stays the only one that refreshes them.
"""

from __future__ import annotations

import pytest

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

_TOKEN = "mirror-pushed-token"
_LOCAL = "studio-pushed-test"
_PUSHER = "nate-node-test"


def _federation(monkeypatch: pytest.MonkeyPatch, *, include_pushed: bool | None) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TOKEN", _TOKEN)
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", _LOCAL)
    if include_pushed is None:
        monkeypatch.delenv("AGENT_LB_FEDERATION_MIRROR_INCLUDE_PUSHED", raising=False)
    else:
        monkeypatch.setenv("AGENT_LB_FEDERATION_MIRROR_INCLUDE_PUSHED", "true" if include_pushed else "false")
    get_settings.cache_clear()


async def _seed(account_id: str, *, owner: str | None, access: str, refresh: str) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider="anthropic",
        chatgpt_account_id=None,
        email=f"{account_id}@example.com",
        alias=None,
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt(access),
        refresh_token_encrypted=encryptor.encrypt(refresh),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()


async def _mirror(async_client) -> tuple[dict[str, dict], str]:
    response = await async_client.get("/api/federation/mirror", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert response.status_code == 200
    return {a["account_id"]: a for a in response.json()["accounts"]}, response.text


@pytest.mark.asyncio
async def test_the_mirror_carries_pushed_in_accounts_with_their_access_token_only(async_client, monkeypatch):
    _federation(monkeypatch, include_pushed=True)
    await _seed("acc_pushed_owned", owner=None, access="owned-access", refresh="owned-refresh")
    await _seed("acc_pushed_from_nate", owner=_PUSHER, access="nate-access", refresh="nate-refresh")

    accounts, body = await _mirror(async_client)

    assert accounts["acc_pushed_owned"]["access_token"] == "owned-access"
    assert accounts["acc_pushed_from_nate"]["access_token"] == "nate-access"
    assert "nate-refresh" not in body and "owned-refresh" not in body
    for account in accounts.values():
        assert "refresh_token" not in account


@pytest.mark.asyncio
async def test_without_the_setting_the_mirror_keeps_only_owned_accounts(async_client, monkeypatch):
    for include in (None, False):
        _federation(monkeypatch, include_pushed=include)
        await _seed(f"acc_owned_{include}", owner=None, access="a", refresh="r")
        await _seed(f"acc_nate_{include}", owner=_PUSHER, access="b", refresh="s")
        accounts, _ = await _mirror(async_client)
        assert f"acc_owned_{include}" in accounts
        assert f"acc_nate_{include}" not in accounts
