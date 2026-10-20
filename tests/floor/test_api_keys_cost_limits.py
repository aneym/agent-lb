from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module
from app.core.openai.models import OpenAIResponsePayload
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from tests.floor.api_keys_api_support import _import_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_chat_completions_stream_finalizes_cost_limit(async_client, monkeypatch):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "chat-completions-cost-limit",
            "limits": [
                {"limitType": "cost_usd", "limitWindow": "weekly", "maxValue": 20_000_000},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_chat_cost_limit", "chat-cost-limit@example.com")

    seen = {"calls": 0}

    async def fake_stream(_payload, _headers, _access_token, _account_id, base_url=None, raise_for_status=False):
        seen["calls"] += 1
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_chat_cost_limit","model":"gpt-5.4",'
            '"service_tier":"default","usage":{"input_tokens":1000000,"output_tokens":1000000,'
            '"total_tokens":2000000}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        _ = [line async for line in response.aiter_lines() if line]

    blocked = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert seen["calls"] == 1

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 27_500_000


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/codex/responses/compact", "/v1/responses/compact"])
@pytest.mark.parametrize("requested_service_tier", ["priority", "fast"])
async def test_compact_cost_limit_uses_canonical_request_service_tier_when_response_omits_echo(
    async_client,
    monkeypatch,
    endpoint,
    requested_service_tier,
):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "compact-priority-cost-limit",
            "limits": [
                {"limitType": "cost_usd", "limitWindow": "weekly", "maxValue": 30_000_000},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_priority_cost", "compact-priority-cost@example.com")

    seen = {"calls": 0}

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        seen["calls"] += 1
        return OpenAIResponsePayload.model_validate(
            {
                "id": "resp_compact_priority_cost",
                "model": "gpt-5.4",
                "status": "completed",
                "usage": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "total_tokens": 2_000_000,
                },
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    request_payload = {
        "model": "gpt-5.4",
        "instructions": "hi",
        "input": [],
        "service_tier": requested_service_tier,
    }

    first = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        json=request_payload,
    )
    assert first.status_code == 200

    blocked = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        json=request_payload,
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert seen["calls"] == 1

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 35_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/codex/responses/compact", "/v1/responses/compact"])
async def test_compact_cost_limit_prefers_response_service_tier_over_request(
    async_client,
    monkeypatch,
    endpoint,
):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "compact-response-tier-cost-limit",
            "limits": [
                {"limitType": "cost_usd", "limitWindow": "weekly", "maxValue": 100_000_000},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_response_tier", "compact-response-tier@example.com")

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        return OpenAIResponsePayload.model_validate(
            {
                "id": "resp_compact_response_tier",
                "model": "gpt-5.4",
                "status": "completed",
                "service_tier": "default",
                "usage": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 1_000_000,
                    "total_tokens": 2_000_000,
                },
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    response = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "service_tier": "priority",
        },
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 27_500_000


@pytest.mark.asyncio
async def test_v1_responses_non_stream_finalizes_cost_limit(async_client, monkeypatch):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "v1-responses-cost-limit",
            "limits": [
                {"limitType": "cost_usd", "limitWindow": "weekly", "maxValue": 30_000_000},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_v1_responses_cost", "v1-responses-cost@example.com")

    seen = {"calls": 0}

    async def fake_stream(_payload, _headers, _access_token, _account_id, base_url=None, raise_for_status=False):
        seen["calls"] += 1
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_v1_cost_limit","model":"gpt-5.4",'
            '"status":"completed","service_tier":"default","usage":{"input_tokens":1000000,"output_tokens":1000000,'
            '"total_tokens":2000000}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.4", "input": "hi"},
    )
    assert response.status_code == 200

    second = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.4", "input": "hi"},
    )
    assert second.status_code == 200

    blocked = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.4", "input": "hi"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert seen["calls"] == 2

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 55_000_000
