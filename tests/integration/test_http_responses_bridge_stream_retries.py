from __future__ import annotations
import json
import pytest
import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _PrecreatedCloseUpstreamWebSocket,
    _ProtocolOnlyTruncatedUpstreamWebSocket,
    _TextThenTruncatedUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _cleanup_http_bridge_sessions,
    _collect_sse_events,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_retries_once_when_upstream_closes_before_response_created(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_retry", "http-bridge-retry@example.com")
    account = await _get_account(account_id)
    upstreams = [_PrecreatedCloseUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        request_stage="first_turn",
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
        api_key=None,
        preferred_account_id=None,
    ):
        del preferred_account_id
        del (
            self,
            deadline,
            request_id,
            kind,
            request_stage,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[connect_count]
        connect_count += 1
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "retry-me",
            "prompt_cache_key": "retry-key",
        },
    )

    assert response.status_code == 200
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_retries_lifecycle_only_truncated_stream(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_protocol_retry",
        "http-bridge-protocol-retry@example.com",
    )
    account = await _get_account(account_id)
    upstreams = [_ProtocolOnlyTruncatedUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        request_stage="first_turn",
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
        api_key=None,
        preferred_account_id=None,
    ):
        del preferred_account_id
        del (
            self,
            deadline,
            request_id,
            kind,
            request_stage,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[connect_count]
        connect_count += 1
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    events = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body={
            "model": "gpt-5.5",
            "instructions": "Return exactly OK.",
            "input": "retry lifecycle-only truncation",
            "prompt_cache_key": "protocol-retry-key",
            "stream": True,
        },
    )

    assert connect_count == 2
    assert [event["type"] for event in events] == [
        "response.created",
        "response.in_progress",
        "response.output_text.delta",
        "response.completed",
    ]
    response_ids = {
        event["response"]["id"]
        for event in events
        if isinstance(event.get("response"), dict) and isinstance(event["response"].get("id"), str)
    }
    assert len(response_ids) == 1
    assert events[-2]["delta"] == "OK"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_retry_after_visible_output(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_visible_no_retry",
        "http-bridge-visible-no-retry@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _TextThenTruncatedUpstreamWebSocket()
    connect_count = 0

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        request_stage="first_turn",
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
        api_key=None,
        preferred_account_id=None,
    ):
        del preferred_account_id
        del (
            self,
            deadline,
            request_id,
            kind,
            request_stage,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        connect_count += 1
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    events = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body={
            "model": "gpt-5.5",
            "instructions": "Return exactly OK.",
            "input": "do not retry after output",
            "prompt_cache_key": "visible-no-retry-key",
            "stream": True,
        },
    )

    assert connect_count == 1
    assert [event["type"] for event in events] == [
        "response.created",
        "response.output_text.delta",
        "response.failed",
    ]
    assert events[1]["delta"] == "partial"
    assert events[-1]["response"]["error"]["code"] == "upstream_stream_truncated"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_rejects_oversized_response_create_before_upstream(
    async_client,
    monkeypatch,
    tmp_path,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 128)
    monkeypatch.setattr(proxy_module, "_OVERSIZED_RESPONSE_CREATE_DUMP_DIR", tmp_path)

    async def fail_get_or_create_http_bridge_session(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("oversized response.create must fail before upstream bridge session allocation")

    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_get_or_create_http_bridge_session",
        fail_get_or_create_http_bridge_session,
    )

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "x" * 256}]}],
            "prompt_cache_key": "oversized-http-bridge",
        },
    )

    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == "payload_too_large"
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["param"] == "input"
    assert "response.create is too large for upstream websocket" in payload["error"]["message"]

    meta_files = list(tmp_path.glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta["reason"]["error_code"] == "payload_too_large"
    assert meta["request"]["transport"] == "http"
    assert meta["request"]["request_text_bytes"] > 128


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_slims_historical_inline_artifacts_and_succeeds(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 640)
    account_id = await _import_account(async_client, "acc_http_bridge_slim", "http-bridge-slim@example.com")
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        request_stage="first_turn",
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
        api_key=None,
        preferred_account_id=None,
    ):
        del preferred_account_id
        del (
            self,
            deadline,
            request_id,
            kind,
            request_stage,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
            api_key,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "old turn"}]},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "data:image/png;base64," + ("A" * 1500),
                },
                {"role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "ping"}]},
            ],
            "prompt_cache_key": "slim-http-bridge",
        },
    )

    assert response.status_code == 200
    sent_payload = json.loads(fake_upstream.sent_text[0])
    assert sent_payload["input"][-1]["content"][0]["text"] == "ping"
    assert "data:image/" not in json.dumps(sent_payload["input"], ensure_ascii=True)
    assert "historical tool output" in json.dumps(sent_payload["input"], ensure_ascii=True)
