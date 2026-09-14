from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from app.modules.proxy import service as proxy


@pytest.mark.parametrize("guard", ["file_pin", "input_file", "input_image", "conversation", "missing", "invalid"])
def test_owner_quota_reset_classifier_refuses_unportable_requests(guard):
    input_value = [{"role": "user", "content": "continue"}]
    if guard == "input_file":
        input_value[0]["content"] = [{"type": "input_file", "file_id": "file_owned"}]
    elif guard == "input_image":
        input_value[0]["content"] = [{"type": "input_image", "file_id": "image_owned"}]
    request_payload = {
        "type": "response.create",
        "previous_response_id": "resp_owner",
        "input": input_value,
    }
    if guard == "conversation":
        request_payload["conversation"] = "conv_owner"
    request_text = json.dumps(request_payload)
    if guard == "missing":
        request_text = None
    elif guard == "invalid":
        request_text = "not-json"
    state = proxy._WebSocketRequestState(
        request_id="req_owner_quota_reset",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0,
        request_text=request_text,
        previous_response_id="resp_owner",
        preferred_account_id="owner",
        file_required_preferred_account=guard == "file_pin",
        expose_stale_previous_response_classifier=True,
    )

    _, payload, _, _ = proxy._rewrite_websocket_previous_response_owner_unavailable_event(request_state=state)

    assert payload is not None
    assert payload["response"]["error"]["code"] == "upstream_unavailable"


@pytest.mark.parametrize("guard", ["none", "file_pin", "file_reference", "short", "visible", "retried", "created"])
async def test_quota_rotation_only_replays_unstarted_complete_unpinned_history(monkeypatch, guard):
    service = proxy.ProxyService(lambda: None)
    finalize = AsyncMock()
    health = AsyncMock()
    monkeypatch.setattr(service, "_finalize_websocket_request_state", finalize)
    monkeypatch.setattr(service, "_handle_stream_error", health)
    full_input = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "next"},
    ]
    if guard == "file_reference":
        full_input[0]["content"] = [{"type": "input_file", "file_id": "file_owned"}]
    fresh = json.dumps({"type": "response.create", "model": "gpt-5.4", "input": full_input})
    state = proxy._WebSocketRequestState(
        request_id="req_rotation",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0,
        awaiting_response_created=True,
        request_text=json.dumps({"previous_response_id": "resp_owner", "input": [full_input[-1]]}),
        previous_response_id="resp_owner",
        preferred_account_id="owner",
        proxy_injected_previous_response_id=True,
        fresh_upstream_request_text=fresh,
        fresh_upstream_request_is_retry_safe=guard != "short",
        file_required_preferred_account=guard == "file_pin",
        downstream_visible=guard == "visible",
        replay_count=int(guard == "retried"),
        response_id="resp_started" if guard == "created" else None,
    )
    control = proxy._WebSocketUpstreamControl()
    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": "error",
                "status": 429,
                "error": {"type": "invalid_request_error", "code": "usage_limit_reached", "message": "limit"},
            }
        ),
        account=SimpleNamespace(id="owner"),
        account_id_value="owner",
        pending_requests=deque([state]),
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=control,
        response_create_gate=asyncio.Semaphore(1),
    )
    if guard == "none":
        assert control.replay_request_state is state
        assert control.suppress_downstream_event
        assert state.previous_response_id is None
        assert state.preferred_account_id is None
        assert state.excluded_account_ids == {"owner"}
        assert json.loads(state.request_text)["input"] == full_input
        assert "previous_response_id" not in json.loads(state.request_text)
        assert state.replay_count == 1
        finalize.assert_not_awaited()
        health.assert_awaited_once()
    else:
        assert control.replay_request_state is None
        assert state.previous_response_id == "resp_owner"
        assert state.preferred_account_id == "owner"
        finalize.assert_awaited_once()
