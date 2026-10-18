from __future__ import annotations

import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.files import FileProxyError
from tests.integration.proxy_files_support import (
    _import_account as _import_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_files_finalize_pins_to_create_account(async_client, monkeypatch):

    await _import_account(async_client, "acc_pin_a", "pin-a@example.com")
    await _import_account(async_client, "acc_pin_b", "pin-b@example.com")

    create_seen: dict[str, object] = {}
    finalize_seen: dict[str, object] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        create_seen["account_id"] = account_id
        return {"file_id": "file_pinned", "upload_url": "https://blob.example/sas?token=p"}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        finalize_seen["account_id"] = account_id
        finalize_seen["file_id"] = file_id
        return {"status": "success", "download_url": "https://blob.example/dl/p"}

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)

    create_resp = await async_client.post(
        "/backend-api/files",
        json={"file_name": "a.png", "file_size": 100, "use_case": "codex"},
    )
    assert create_resp.status_code == 200
    creating_account = create_seen["account_id"]
    assert creating_account in {"acc_pin_a", "acc_pin_b"}

    finalize_resp = await async_client.post("/backend-api/files/file_pinned/uploaded")
    assert finalize_resp.status_code == 200
    assert finalize_seen["file_id"] == "file_pinned"

    assert finalize_seen["account_id"] == creating_account


@pytest.mark.asyncio
async def test_backend_files_finalize_pinned_401_retry_fails_closed(async_client, monkeypatch):

    await _import_account(async_client, "acc_pin_401_a", "pin-401-a@example.com")
    await _import_account(async_client, "acc_pin_401_b", "pin-401-b@example.com")

    create_seen: dict[str, str | None] = {}
    finalize_account_ids: list[str | None] = []

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        create_seen["account_id"] = account_id
        return {"file_id": "file_pinned_401", "upload_url": "https://blob.example/sas?token=pin401"}

    async def fake_finalize_file(*, file_id, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        del file_id, headers, access_token, base_url, session
        finalize_account_ids.append(account_id)
        if account_id == create_seen["account_id"]:
            raise FileProxyError(
                401,
                {"error": {"message": "token invalidated", "type": "authentication_error", "code": "invalid_api_key"}},
            )
        return {"status": "success", "download_url": "https://blob.example/wrong-account"}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module, "core_finalize_file", fake_finalize_file)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    create_resp = await async_client.post(
        "/backend-api/files",
        json={"file_name": "a.png", "file_size": 100, "use_case": "codex"},
    )
    assert create_resp.status_code == 200
    creating_account = create_seen["account_id"]
    assert creating_account in {"acc_pin_401_a", "acc_pin_401_b"}

    finalize_resp = await async_client.post("/backend-api/files/file_pinned_401/uploaded")
    assert finalize_resp.status_code == 401
    assert finalize_resp.json()["error"]["code"] == "invalid_api_key"
    assert finalize_account_ids == [creating_account, creating_account]


@pytest.mark.asyncio
async def test_resolve_file_account_for_responses_returns_pin_when_no_other_affinity(async_client):

    await _import_account(async_client, "acc_resp_a", "resp-a@example.com")
    await _import_account(async_client, "acc_resp_b", "resp-b@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)

    await service._pin_file_account("file_response_pin", "acc_resp_a")

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Summarize the file."},
                        {"type": "input_file", "file_id": "file_response_pin"},
                    ],
                }
            ],
        }
    )
    resolved = await service._resolve_file_account_for_responses(payload, {})
    assert resolved == "acc_resp_a"

    ws_payload = dict(payload.to_payload())
    ws_payload["type"] = "response.create"
    prepared = await service._prepare_websocket_response_create_request(
        ws_payload,
        headers={},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        api_key=None,
    )
    assert prepared.request_state.preferred_account_id == "acc_resp_a"


@pytest.mark.asyncio
async def test_v1_responses_prompt_cache_key_overrides_file_id_pin(async_client, monkeypatch):

    await _import_account(async_client, "acc_pck_a", "pck-a@example.com")
    await _import_account(async_client, "acc_pck_b", "pck-b@example.com")

    create_account_holder: dict[str, str] = {}

    async def fake_create_file(*, payload, headers, access_token, account_id, base_url=None, session=None, **kwargs):
        create_account_holder["account_id"] = account_id
        return {"file_id": "file_pck", "upload_url": "https://blob.example/sas?t=p"}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            "event: response.completed\ndata: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_pck",
                        "object": "response",
                        "status": "completed",
                        "created_at": 0,
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                            "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens_details": {"reasoning_tokens": 0},
                        },
                    },
                }
            )
            + "\n\n"
        )

    monkeypatch.setattr(proxy_module, "core_create_file", fake_create_file)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    create_resp = await async_client.post(
        "/backend-api/files",
        json={"file_name": "x.png", "file_size": 100, "use_case": "codex"},
    )
    assert create_resp.status_code == 200

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Continue."},
                        {"type": "input_file", "file_id": "file_pck"},
                    ],
                }
            ],
            "prompt_cache_key": "thread-123",
        }
    )
    resolved = await service._resolve_file_account_for_responses(payload, {})
    assert resolved is None


@pytest.mark.asyncio
async def test_derived_prompt_cache_key_does_not_block_file_id_pin(async_client):

    await _import_account(async_client, "acc_derived_a", "derived-a@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app

    service = get_proxy_service_for_app(async_client._transport.app)
    await service._pin_file_account("file_derived", "acc_derived_a")

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "What's in this?"},
                        {"type": "input_file", "file_id": "file_derived"},
                    ],
                }
            ],
        }
    )

    payload.prompt_cache_key = "derived-key-load-balancer-set"
    payload.model_fields_set.discard("prompt_cache_key")
    assert "prompt_cache_key" not in payload.model_fields_set

    resolved = await service._resolve_file_account_for_responses(payload, {})
    assert resolved == "acc_derived_a"


@pytest.mark.asyncio
async def test_synthesized_turn_state_does_not_block_file_id_pin(async_client):

    await _import_account(async_client, "acc_ts_a", "ts-a@example.com")

    from app.core.openai.requests import ResponsesRequest
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.affinity import ensure_downstream_turn_state

    service = get_proxy_service_for_app(async_client._transport.app)
    await service._pin_file_account("file_synth_ts", "acc_ts_a")

    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.2",
            "instructions": "You are a helpful assistant.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Describe."},
                        {"type": "input_file", "file_id": "file_synth_ts"},
                    ],
                }
            ],
        }
    )

    synth_turn_state = ensure_downstream_turn_state({})
    assert synth_turn_state.startswith("turn_")
    headers_synth = {"x-codex-turn-state": synth_turn_state}
    resolved = await service._resolve_file_account_for_responses(payload, headers_synth)
    assert resolved == "acc_ts_a"

    headers_client = {"x-codex-turn-state": "client-conversation-handle-42"}
    resolved_blocked = await service._resolve_file_account_for_responses(payload, headers_client)
    assert resolved_blocked is None
