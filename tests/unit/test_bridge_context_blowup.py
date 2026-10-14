

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.core.errors import openai_error
from app.db.models import AccountStatus
from app.modules.proxy import service as proxy_service

pytestmark = [pytest.mark.unit, pytest.mark.asyncio(loop_scope="session")]


def _make_session(*, closed: bool = False) -> proxy_service._HTTPBridgeSession:

    return proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-test", None),
        headers={"x-codex-session-id": "sid-test"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-test",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=time.time(),
        idle_ttl_seconds=120.0,
        closed=closed,
    )


def _make_request_state(
    *,
    previous_response_id: str | None = None,
) -> proxy_service._WebSocketRequestState:

    return proxy_service._WebSocketRequestState(
        request_id="req-test-1",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.time(),
        event_queue=asyncio.Queue(),
        transport="http",
        previous_response_id=previous_response_id,
    )


class TestSubmitClosedSessionWithPreviousResponseId:


    async def test_closed_session_with_previous_response_id_raises_502(self):

        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=True)
        request_state = _make_request_state(
            previous_response_id="resp_abc123",
        )

        with pytest.raises(ProxyResponseError) as exc_info:
            await service._submit_http_bridge_request(
                session=session,
                request_state=request_state,
                text_data='{"type":"response.create","previous_response_id":"resp_abc123"}',
                queue_limit=10,
            )


        assert exc_info.value.status_code == 502
        error_payload = exc_info.value.payload
        assert error_payload["error"]["code"] == "upstream_unavailable"

        assert "previous_response_not_found" not in str(error_payload)

    async def test_closed_session_without_previous_response_id_attempts_recovery(
        self,
        monkeypatch,
    ):

        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=True)
        service._http_bridge_sessions[session.key] = session
        request_state = _make_request_state(previous_response_id=None)


        retry_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(
            service,
            "_retry_http_bridge_request_on_fresh_upstream",
            retry_mock,
        )

        monkeypatch.setattr(
            service,
            "_maybe_prewarm_http_bridge_session",
            AsyncMock(),
        )



        event_queue = request_state.event_queue
        assert event_queue is not None
        await event_queue.put(None)



        try:
            await service._submit_http_bridge_request(
                session=session,
                request_state=request_state,
                text_data='{"type":"response.create"}',
                queue_limit=10,
            )
        except Exception:
            pass


        assert retry_mock.call_count >= 1

        first_call = retry_mock.call_args_list[0]
        assert first_call.kwargs.get("send_request") is False


class TestMidRequestFailurePreservesPreviousResponseId:


    async def test_mid_request_failure_with_previous_response_id_raises_502(
        self,
        monkeypatch,
    ):

        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=False)
        request_state = _make_request_state(
            previous_response_id="resp_abc123",
        )


        async def failing_send(*args, **kwargs):
            raise ConnectionError("upstream websocket died")

        session.upstream = cast(
            Any,
            SimpleNamespace(
                send_text=failing_send,
                close=AsyncMock(),
            ),
        )

        monkeypatch.setattr(
            service,
            "_fail_pending_websocket_requests",
            AsyncMock(),
        )

        with pytest.raises(ProxyResponseError) as exc_info:
            await service._submit_http_bridge_request(
                session=session,
                request_state=request_state,
                text_data='{"type":"response.create","previous_response_id":"resp_abc123"}',
                queue_limit=10,
            )


        assert exc_info.value.status_code == 502, (
            f"Expected 502 upstream_unavailable but got {exc_info.value.status_code}. "
            f"If this is 400, the bug is present: the CLI will drop "
            f"previous_response_id and resend full conversation (70K tok/turn)."
        )
        assert exc_info.value.payload["error"]["code"] in ("upstream_unavailable", "bridge_owner_unreachable")
        assert "previous_response_not_found" not in str(exc_info.value.payload)


class TestRetryHelperPreservesPreviousResponseId:


    async def test_retry_with_previous_response_id_returns_false_without_marking_error(self):

        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=True)
        request_state = _make_request_state(
            previous_response_id="resp_xyz789",
        )

        result = await service._retry_http_bridge_request_on_fresh_upstream(
            session=session,
            request_state=request_state,
            text_data='{"type":"response.create","previous_response_id":"resp_xyz789"}',
            send_request=True,
        )


        assert result is False

        assert request_state.error_code_override != "previous_response_not_found"
        assert request_state.error_code_override is None

    async def test_reconnect_only_recovery_with_previous_response_id_skips_resend(self, monkeypatch):
        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=True)
        send_text = AsyncMock()
        session.upstream = cast(Any, SimpleNamespace(send_text=send_text))
        request_state = _make_request_state(previous_response_id="resp_xyz789")

        reconnect_mock = AsyncMock()
        monkeypatch.setattr(service, "_reconnect_http_bridge_session", reconnect_mock)

        result = await service._retry_http_bridge_request_on_fresh_upstream(
            session=session,
            request_state=request_state,
            text_data='{"type":"response.create","previous_response_id":"resp_xyz789"}',
            send_request=False,
        )

        assert result is True
        reconnect_mock.assert_awaited_once()
        send_text.assert_not_awaited()

    async def test_retry_with_proxy_injected_previous_response_replays_full_request(self, monkeypatch):

        service = proxy_service.ProxyService(cast(Any, nullcontext()))
        session = _make_session(closed=False)
        request_state = _make_request_state(previous_response_id="resp_xyz789")
        request_state.proxy_injected_previous_response_id = True
        request_state.fresh_upstream_request_text = '{"type":"response.create","input":"hello"}'



        request_state.fresh_upstream_request_is_retry_safe = True
        request_state.request_text = '{"type":"response.create","previous_response_id":"resp_xyz789","input":"delta"}'

        send_text = AsyncMock()
        session.upstream = cast(Any, SimpleNamespace(send_text=send_text, close=AsyncMock()))
        monkeypatch.setattr(service, "_reconnect_http_bridge_session", AsyncMock())

        result = await service._retry_http_bridge_request_on_fresh_upstream(
            session=session,
            request_state=request_state,
            text_data='{"type":"response.create","previous_response_id":"resp_xyz789","input":"delta"}',
            send_request=True,
        )

        assert result is True
        send_text.assert_awaited_once_with('{"type":"response.create","input":"hello"}')
        assert request_state.previous_response_id is None
        assert request_state.proxy_injected_previous_response_id is False
        assert request_state.request_text == '{"type":"response.create","input":"hello"}'


class TestContextGrowthScenarios:


    async def test_healthy_growth_rate_stays_within_budget(self):

        system_prompt_tokens = 50_000
        content_per_turn = 2_300
        context_window = 876_000
        max_turns = 200

        context = system_prompt_tokens
        for turn in range(max_turns):
            context += content_per_turn
            if context >= context_window:
                pytest.fail(
                    f"Context exceeded window at turn {turn} "
                    f"({context:,} >= {context_window:,}). "
                    f"Healthy sessions should last 200+ turns."
                )

        assert context < context_window

    async def test_broken_growth_rate_fills_window_fast(self):

        system_prompt_tokens = 50_000
        growth_per_turn = 70_000
        context_window = 876_000

        context = system_prompt_tokens
        compaction_turn = None
        for turn in range(200):
            context += growth_per_turn
            if context >= context_window:
                compaction_turn = turn
                break

        assert compaction_turn is not None
        assert compaction_turn < 20

    async def test_error_code_determines_growth_rate(self):

        error_502 = ProxyResponseError(
            502,
            openai_error("upstream_unavailable", "closed"),
        )
        assert error_502.status_code == 502

        error_400 = ProxyResponseError(
            400,
            openai_error("previous_response_not_found", "Previous response not found"),
        )
        assert error_400.status_code == 400
        assert error_400.payload["error"]["code"] == "previous_response_not_found"
