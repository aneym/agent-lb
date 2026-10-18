from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from tests.integration.proxy_compact_support import (
    _make_auth_json,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_proxy_compact_retry_uses_refreshed_account_id(async_client, monkeypatch):
    email = "compact-retry@example.com"
    raw_account_id = "acc_compact_retry_old"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    captured_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        captured_account_ids.append(account_id)
        if len(captured_account_ids) == 1:
            raise ProxyResponseError(
                401,
                openai_error("invalid_api_key", "token expired"),
            )
        return OpenAIResponsePayload.model_validate({"output": []})

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        if force:
            account.chatgpt_account_id = "acc_compact_retry_new"
        return account

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["output"] == []
    assert captured_account_ids == ["acc_compact_retry_old", "acc_compact_retry_new"]


@pytest.mark.asyncio
async def test_proxy_compact_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    first_email = "compact-invalidated-a@example.com"
    first_raw_account_id = "acc_compact_invalidated_a"
    first_auth_json = _make_auth_json(first_raw_account_id, first_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-a.json", json.dumps(first_auth_json), "application/json")},
    )
    assert response.status_code == 200

    second_email = "compact-invalidated-b@example.com"
    second_raw_account_id = "acc_compact_invalidated_b"
    second_auth_json = _make_auth_json(second_raw_account_id, second_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-b.json", json.dumps(second_auth_json), "application/json")},
    )
    assert response.status_code == 200

    first_account_id = generate_unique_account_id(first_raw_account_id, first_email)
    second_account_id = generate_unique_account_id(second_raw_account_id, second_email)
    first_upstream_account_id = "chatgpt_compact_invalidated_a"
    second_upstream_account_id = "chatgpt_compact_invalidated_b"

    async with SessionLocal() as session:
        first_account = await session.get(Account, first_account_id)
        assert first_account is not None
        first_account.chatgpt_account_id = first_upstream_account_id
        second_account = await session.get(Account, second_account_id)
        assert second_account is not None
        second_account.chatgpt_account_id = second_upstream_account_id
        await session.commit()

    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise ProxyResponseError(
                401,
                openai_error(
                    "invalid_api_key",
                    "Your authentication token has been invalidated. Please try signing in again.",
                    error_type="authentication_error",
                ),
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        return account

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] in {first_upstream_account_id, second_upstream_account_id}
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_proxy_compact_token_invalidated_marks_reauth_and_fails_over(async_client, monkeypatch):
    first_email = "compact-token-invalidated-a@example.com"
    first_raw_account_id = "acc_compact_token_invalidated_a"
    first_auth_json = _make_auth_json(first_raw_account_id, first_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-a.json", json.dumps(first_auth_json), "application/json")},
    )
    assert response.status_code == 200

    second_email = "compact-token-invalidated-b@example.com"
    second_raw_account_id = "acc_compact_token_invalidated_b"
    second_auth_json = _make_auth_json(second_raw_account_id, second_email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth-b.json", json.dumps(second_auth_json), "application/json")},
    )
    assert response.status_code == 200

    first_account_id = generate_unique_account_id(first_raw_account_id, first_email)
    second_account_id = generate_unique_account_id(second_raw_account_id, second_email)
    first_upstream_account_id = "chatgpt_compact_token_invalidated_a"
    second_upstream_account_id = "chatgpt_compact_token_invalidated_b"

    async with SessionLocal() as session:
        first_account = await session.get(Account, first_account_id)
        assert first_account is not None
        first_account.chatgpt_account_id = first_upstream_account_id
        second_account = await session.get(Account, second_account_id)
        assert second_account is not None
        second_account.chatgpt_account_id = second_upstream_account_id
        await session.commit()

    captured_account_ids: list[str | None] = []
    invalidated_upstream_account_id: str | None = None

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal invalidated_upstream_account_id
        if invalidated_upstream_account_id is None:
            invalidated_upstream_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_upstream_account_id:
            raise ProxyResponseError(
                401,
                openai_error(
                    "token_invalidated",
                    "Your authentication token has been invalidated. Please try signing in again.",
                    error_type="authentication_error",
                ),
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        return account

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert captured_account_ids[:2] == [invalidated_upstream_account_id, invalidated_upstream_account_id]
    assert captured_account_ids[2] in {first_upstream_account_id, second_upstream_account_id}
    assert captured_account_ids[2] != invalidated_upstream_account_id

    invalidated_account_id = (
        first_account_id if invalidated_upstream_account_id == first_upstream_account_id else second_account_id
    )

    async with SessionLocal() as session:
        invalidated_account = await session.get(Account, invalidated_account_id)
        assert invalidated_account is not None
        assert invalidated_account.status == AccountStatus.REAUTH_REQUIRED
        assert invalidated_account.deactivation_reason is not None
        assert "re-login required" in invalidated_account.deactivation_reason

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = {account["accountId"]: account for account in accounts_response.json()["accounts"]}
    assert accounts[invalidated_account_id]["status"] == "reauth_required"

    overview_response = await async_client.get("/api/dashboard/overview")
    assert overview_response.status_code == 200
    overview_accounts = {account["accountId"]: account for account in overview_response.json()["accounts"]}
    assert overview_accounts[invalidated_account_id]["status"] == "reauth_required"


@pytest.mark.asyncio
async def test_proxy_compact_repeated_401_settles_reservation_if_error_recording_fails(async_client, monkeypatch):
    email = "compact-invalidated-settle@example.com"
    raw_account_id = "acc_compact_invalidated_settle"
    auth_json = _make_auth_json(raw_account_id, email)
    response = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")},
    )
    assert response.status_code == 200

    compact_calls = 0

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal compact_calls
        compact_calls += 1
        raise ProxyResponseError(
            401,
            openai_error(
                "invalid_api_key",
                "Your authentication token has been invalidated. Please try signing in again.",
                error_type="authentication_error",
            ),
        )

    async def fake_ensure_fresh(self, account, *, force: bool = False, timeout_seconds=None):
        return account

    async def fake_handle_proxy_error(self, account, exc):
        raise RuntimeError("account health store unavailable")

    settle_compact_usage = AsyncMock()

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_proxy_error", fake_handle_proxy_error)
    monkeypatch.setattr(proxy_module.ProxyService, "_settle_compact_api_key_usage", settle_compact_usage)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    with pytest.raises(RuntimeError, match="account health store unavailable"):
        await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert compact_calls == 2
    settle_compact_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_proxy_compact_retryable_transport_failure_retries_same_contract_only(async_client, monkeypatch):
    email = "compact-safe-retry@example.com"
    raw_account_id = "acc_compact_safe_retry"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    compact_calls: list[str | None] = []
    stream_calls: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_calls.append(account_id)
        if len(compact_calls) == 1:
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "temporary compact failure"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "output": [{"type": "reasoning", "encrypted_content": "enc_retry_success"}],
            }
        )

    async def fake_stream(*args, **kwargs):
        stream_calls.append("called")
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert compact_calls == [raw_account_id, raw_account_id]
    assert stream_calls == []
