from __future__ import annotations
import hashlib
import pytest
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountTransferDirection, FederationUsageDaily, RequestLog
from app.db.session import SessionLocal


_FEDERATION_TOKEN = "peer-secret-token"


_TRANSFER_TOKEN = "transfer-test-token"


_LOCAL_INSTANCE_ID = "studio-test"


_TAKER_INSTANCE_ID = "laptop-test"


_OTHER_INSTANCE_ID = "other-instance-test"


def _enable_federation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TOKEN", _FEDERATION_TOKEN)
    monkeypatch.setenv(
        "AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(_TRANSFER_TOKEN.encode()).hexdigest()
    )
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", _TAKER_INSTANCE_ID)
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", _LOCAL_INSTANCE_ID)
    get_settings.cache_clear()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_FEDERATION_TOKEN}"}


def _transfer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TRANSFER_TOKEN}"}


async def _seed_account(
    account_id: str,
    *,
    owner_instance: str | None,
    access_token: str = "seed-access",
    refresh_token: str = "seed-refresh",
) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider="anthropic",
        chatgpt_account_id=None,
        email=f"{account_id}@example.com",
        alias="seed-alias",
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt(access_token),
        refresh_token_encrypted=encryptor.encrypt(refresh_token),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner_instance
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()


async def _get_account(account_id: str) -> Account:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        return account


def _request_log_row(source_row_id: int, request_id: str, account_id: str | None) -> dict[str, object]:
    return {
        "source_row_id": source_row_id,
        "account_id": account_id,
        "provider": "anthropic",
        "api_key_id": "edge-local-key",
        "request_id": request_id,
        "request_kind": "normal",
        "requested_at": "2026-10-03T15:00:00Z",
        "model": "claude-opus",
        "status": "success",
        "room": "lab",
        "caller_seat": "alex",
        "unified_5h_utilization": 0.25,
        "unified_7d_utilization": 0.5,
        "input_tokens": 11,
    }
