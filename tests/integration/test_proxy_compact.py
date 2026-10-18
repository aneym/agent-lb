from __future__ import annotations

import contextlib
import json
from typing import cast

import pytest

import app.core.clients.proxy as proxy_client_module
import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.usage.repository import UsageRepository
from tests.integration.proxy_compact_support import (
    _JsonResponse,
    _JsonSession,
    _make_auth_json,
    _session_call_json,
    _session_call_url,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_proxy_compact_no_accounts(async_client):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_proxy_compact_strips_tool_fields_before_upstream(async_client, monkeypatch):
    email = "compact-tools@example.com"
    raw_account_id = "acc_compact_tools"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_payloads: list[dict[str, object]] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del headers, access_token, account_id
        seen_payloads.append(cast(dict[str, object], payload.to_payload()))
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tools": [{"type": "image_generation"}],
        "tool_choice": {"type": "image_generation"},
        "parallel_tool_calls": True,
    }
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert len(seen_payloads) == 1
    assert seen_payloads[0]["model"] == "gpt-5.1"
    assert seen_payloads[0]["instructions"] == "hi"
    assert seen_payloads[0]["input"] == []
    assert "tools" not in seen_payloads[0]
    assert "tool_choice" not in seen_payloads[0]
    assert "parallel_tool_calls" not in seen_payloads[0]


@pytest.mark.asyncio
async def test_proxy_compact_success(async_client, monkeypatch):
    email = "compact@example.com"
    raw_account_id = "acc_compact"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    seen = {}

    async def fake_compact(payload, headers, access_token, account_id):
        seen["access_token"] = access_token
        seen["account_id"] = account_id
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=expected_account_id,
            used_percent=25.0,
            window="primary",
            reset_at=1735689600,
            recorded_at=utcnow(),
            credits_has=True,
            credits_unlimited=False,
            credits_balance=12.5,
        )

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["output"] == []
    assert seen["access_token"] == "access-token"
    assert seen["account_id"] == raw_account_id
    assert response.headers.get("x-codex-primary-used-percent") == "25.0"
    assert response.headers.get("x-codex-primary-window-minutes") == "300"
    assert response.headers.get("x-codex-primary-reset-at") == "1735689600"
    assert response.headers.get("x-codex-credits-has-credits") == "true"
    assert response.headers.get("x-codex-credits-unlimited") == "false"
    assert response.headers.get("x-codex-credits-balance") == "12.50"


@pytest.mark.asyncio
async def test_proxy_compact_success_preserves_compaction_payload(async_client, monkeypatch):
    email = "compact-pass-through@example.com"
    raw_account_id = "acc_compact_pass_through"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    session = _JsonSession(
        _JsonResponse(
            {
                "object": "response.compaction",
                "compaction_summary": {
                    "encrypted_content": "enc_compact_summary_1",
                    "summary_text": "condensed thread state",
                },
            }
        )
    )

    @contextlib.asynccontextmanager
    async def lease_session(session_override=None):
        assert session_override is None
        yield session

    monkeypatch.setattr(proxy_client_module, "lease_http_session", lease_session)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "response.compaction"
    assert body["compaction_summary"] == {
        "encrypted_content": "enc_compact_summary_1",
        "summary_text": "condensed thread state",
    }
    assert _session_call_url(session).endswith("/codex/responses/compact")
    call_json = _session_call_json(session)
    assert "stream" not in call_json
    assert "store" not in call_json


@pytest.mark.asyncio
async def test_proxy_compact_masks_previous_response_not_found(async_client, monkeypatch):
    email = "compact-prev-missing@example.com"
    raw_account_id = "acc_compact_prev_missing"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token, account_id
        error_payload = openai_error(
            "invalid_request_error",
            "Previous response with id 'resp_compact_missing' not found.",
            error_type="invalid_request_error",
        )
        error_payload["error"]["param"] = "previous_response_id"
        raise ProxyResponseError(400, error_payload)

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "stream_incomplete"
    assert body["error"]["message"] == "Upstream websocket closed before response.completed"
    assert "resp_compact_missing" not in response.text


@pytest.mark.asyncio
async def test_proxy_compact_output_round_trips_into_followup_responses_without_pruning(async_client, monkeypatch):
    email = "compact-round-trip@example.com"
    raw_account_id = "acc_compact_round_trip"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    compact_window = {
        "object": "response.compaction",
        "output": [
            {
                "type": "message",
                "id": "msg_compact_round_trip",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "preserve me exactly"}],
            },
            {"type": "reasoning", "encrypted_content": "enc_round_trip_state"},
        ],
        "retained_items": [{"type": "item_reference", "id": "msg_original_round_trip"}],
    }
    seen_inputs: list[object] = []

    async def fake_compact(payload, headers, access_token, account_id):
        return CompactResponsePayload.model_validate(compact_window)

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_inputs.append(payload.input)
        yield 'data: {"type":"response.completed","response":{"id":"resp_round_trip"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    compact_payload = {"model": "gpt-5.1", "instructions": "compact", "input": []}
    compact_response = await async_client.post("/backend-api/codex/responses/compact", json=compact_payload)
    assert compact_response.status_code == 200
    assert compact_response.json() == compact_window

    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "continue",
        "input": compact_response.json()["output"],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload)

    assert response.status_code == 200
    assert seen_inputs == [compact_window["output"]]
