from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus


@pytest.fixture
def openai_credit_override(monkeypatch):
    monkeypatch.setenv("AGENT_LB_OPENAI_ROUTE_TO_CREDITS", "true")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(
    account_id: str | None,
    email: str,
    plan_type: str = "plus",
    *,
    workspace_id: str | None = None,
    workspace_label: str | None = None,
    seat_type: str | None = None,
) -> dict:
    auth_claims = {"chatgpt_plan_type": plan_type}
    if workspace_id:
        auth_claims["workspace_id"] = workspace_id
    if workspace_label:
        auth_claims["workspace_label"] = workspace_label
    if seat_type:
        auth_claims["seat_type"] = seat_type
    payload = {
        "email": email,
        "https://api.openai.com/auth": auth_claims,
    }
    if account_id:
        payload["chatgpt_account_id"] = account_id
    tokens: dict[str, object] = {
        "idToken": _encode_jwt(payload),
        "accessToken": "access",
        "refreshToken": "refresh",
    }
    if account_id:
        tokens["accountId"] = account_id
    return {"tokens": tokens}


def _make_account(account_id: str, email: str, plan_type: str = "plus") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _iso_utc(epoch_seconds: int) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
