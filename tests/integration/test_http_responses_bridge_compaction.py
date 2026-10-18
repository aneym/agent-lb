from __future__ import annotations
import json
import pytest
import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _CompactionBridgeUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeUpstreamMessage,
    _cleanup_http_bridge_sessions,
    _collect_sse_events,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_reinjects_compaction_items_into_completed_envelope(
    async_client,
    monkeypatch,
):

    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_compaction",
        "backend-http-bridge-compaction@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _CompactionBridgeUpstreamWebSocket()

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
        return fake_upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.5",
        "instructions": "compact the conversation",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "What is 2+2?"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "4"}]},
            {"type": "compaction_trigger"},
        ],
        "store": False,
        "stream": True,
    }
    events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body=payload,
        headers={"user-agent": "codex_exec/0.144.0 (Mac OS 26.3.0; arm64) kitty/unknown (codex_exec; 0.144.0)"},
    )

    done_events = [event for event in events if event.get("type") == "response.output_item.done"]
    assert len(done_events) == 1
    assert done_events[0]["item"]["type"] == "compaction"
    assert done_events[0]["item"]["encrypted_content"] == "opaque-account-bound-blob"

    completed_events = [event for event in events if event.get("type") == "response.completed"]
    assert len(completed_events) == 1
    completed_output = completed_events[0]["response"]["output"]
    assert [item.get("type") for item in completed_output] == ["compaction"]
    assert completed_output[0]["encrypted_content"] == "opaque-account-bound-blob"

    upstream_payload = json.loads(fake_upstream.sent_text[0])
    assert {"type": "compaction_trigger"} in upstream_payload["input"]


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_leaves_nonempty_completed_output_untouched(
    async_client,
    monkeypatch,
):

    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_compaction_full",
        "backend-http-bridge-compaction-full@example.com",
    )
    account = await _get_account(account_id)

    class _FullEnvelopeUpstream(_CompactionBridgeUpstreamWebSocket):
        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            response_id = f"resp_compact_full_{len(self.sent_text)}"
            for event in (
                {
                    "type": "response.created",
                    "response": {"id": response_id, "object": "response", "status": "in_progress"},
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": dict(self.compaction_item),
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "status": "completed",
                        "output": [{**self.compaction_item, "id": "cmp_from_upstream"}],
                    },
                },
            ):
                await self._messages.put(_FakeUpstreamMessage("text", text=json.dumps(event, separators=(",", ":"))))

    fake_upstream = _FullEnvelopeUpstream()

    async def fake_select_account_with_budget(self, deadline, **kwargs):
        del self, deadline, kwargs
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

    payload = {
        "model": "gpt-5.5",
        "instructions": "compact the conversation",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "compaction_trigger"},
        ],
        "store": False,
        "stream": True,
    }
    events = await _collect_sse_events(async_client, "/backend-api/codex/responses", json_body=payload)

    completed_events = [event for event in events if event.get("type") == "response.completed"]
    assert len(completed_events) == 1
    completed_output = completed_events[0]["response"]["output"]
    assert [item.get("id") for item in completed_output] == ["cmp_from_upstream"]
