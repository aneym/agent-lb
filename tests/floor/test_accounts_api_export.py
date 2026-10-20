from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import generate_unique_account_id, parse_auth_json

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


@pytest.mark.asyncio
async def test_import_glm_api_key_account_rejects_refresh_token(async_client):

    response = await async_client.post(
        "/api/accounts/import/api-key",
        json={"provider": "glm", "apiKey": "zai-key", "refreshToken": "zai-other"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_api_key_account"

    accounts = (await async_client.get("/api/accounts")).json()["accounts"]
    assert all(account["provider"] != "glm" for account in accounts)


@pytest.mark.asyncio
async def test_export_account_returns_latest_codex_auth_json_with_no_store_headers(async_client):
    email = "export@example.com"
    raw_account_id = "acc_export"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    export = await async_client.post(f"/api/accounts/{expected_account_id}/export")
    assert export.status_code == 200
    assert export.headers["cache-control"] == "no-store, no-cache, must-revalidate, private"
    assert export.headers["pragma"] == "no-cache"
    assert export.headers["expires"] == "0"

    payload = export.json()
    assert payload["accountId"] == expected_account_id
    assert payload["email"] == email
    assert payload["planType"] == "plus"
    assert payload["status"] == "active"

    parsed_auth = parse_auth_json(payload["authJson"].encode("utf-8"))
    assert parsed_auth.tokens.access_token == "access"
    assert parsed_auth.tokens.refresh_token == "refresh"
    assert parsed_auth.tokens.account_id == raw_account_id
    assert parsed_auth.last_refresh_at is not None


@pytest.mark.asyncio
async def test_export_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/export")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"
