from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from tests.integration.proxy_transient_retry_support import (
    _import_account,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _force_usage_weighted_routing(async_client) -> None:
    current = await async_client.get("/api/settings")
    assert current.status_code == 200
    payload = current.json()
    payload["routingStrategy"] = "usage_weighted"
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_compact_500_succeeds_on_second_try_same_account(async_client, monkeypatch):

    await _import_account(async_client, "acc_c500_1", "c500@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "An error occurred while processing your request."),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_compact_500_succeeds_on_third_try(async_client, monkeypatch):

    await _import_account(async_client, "acc_c500_3", "c500_3@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count <= 2:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "server error"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    assert len(seen_account_ids) == 3
    assert len(set(seen_account_ids)) == 1


@pytest.mark.asyncio
async def test_compact_500_exhausts_retries_then_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_cfo_a", "cfo_a@example.com")
    await _import_account(async_client, "acc_cfo_b", "cfo_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_cfo_a":
            raise ProxyResponseError(
                500,
                openai_error("server_error", "server error"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    a_calls = [aid for aid in seen_account_ids if aid == "acc_cfo_a"]
    b_calls = [aid for aid in seen_account_ids if aid == "acc_cfo_b"]
    assert len(a_calls) == 3
    assert len(b_calls) >= 1


@pytest.mark.asyncio
async def test_compact_quota_exceeded_transparent_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_compact_quota_a", "compactquotaa@example.com")
    await _import_account(async_client, "acc_compact_quota_b", "compactquotab@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_compact_quota_a":
            raise ProxyResponseError(
                429,
                openai_error("quota_exceeded", "quota exceeded"),
                failure_phase="status",
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert seen_account_ids[:2] == ["acc_compact_quota_a", "acc_compact_quota_b"]

    async with SessionLocal() as session:
        account_id = generate_unique_account_id("acc_compact_quota_a", "compactquotaa@example.com")
        account_a = await session.get(Account, account_id)
        assert account_a is not None
        await session.refresh(account_a)
        assert account_a.status == AccountStatus.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_compact_500_all_accounts_exhausted(async_client, monkeypatch):

    await _import_account(async_client, "acc_call_a", "call_a@example.com")

    async def fake_compact(payload, headers, access_token, account_id):
        raise ProxyResponseError(
            500,
            openai_error("server_error", "persistent error"),
            failure_phase="status",
            retryable_same_contract=True,
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code in (500, 503)


@pytest.mark.asyncio
async def test_compact_non_500_error_not_retried_as_transient(async_client, monkeypatch):

    await _import_account(async_client, "acc_c400", "c400@example.com")

    call_count = 0

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        raise ProxyResponseError(
            400,
            openai_error("invalid_request_error", "bad request"),
            failure_phase="status",
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 400

    assert call_count == 1


@pytest.mark.asyncio
async def test_compact_502_still_uses_safe_retry_budget(async_client, monkeypatch):

    await _import_account(async_client, "acc_c502", "c502@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "bad gateway"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    assert call_count == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_compact_sticky_503_unknown_code_excludes_failing_account_on_failover(async_client, monkeypatch):
    await _import_account(async_client, "acc_sticky_503_a", "sticky503a@example.com")
    await _import_account(async_client, "acc_sticky_503_b", "sticky503b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_sticky_503_a":
            raise ProxyResponseError(
                503,
                openai_error("bad_gateway", "Bad gateway"),
                failure_phase="status",
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=payload,
        headers={"x-codex-session-id": "sticky-session-503"},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert seen_account_ids[:2] == ["acc_sticky_503_a", "acc_sticky_503_b"]
