from __future__ import annotations
import asyncio
import base64
import contextlib
import json
import time
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
import anyio
import pytest
import pytest_asyncio
from sqlalchemy import select
import app.modules.proxy.service as proxy_module
from app.core.config.settings import Settings, get_settings
from app.core.utils.request_id import reset_request_id, set_request_id
from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app

pytestmark = pytest.mark.integration


_TEST_SYNC_TIMEOUT_SECONDS = 5.0


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_http_bridge_sessions(app_instance):
    yield
    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        sessions = list(service._http_bridge_sessions.values())
        inflight_sessions = list(service._http_bridge_inflight_sessions.values())
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()
        service._http_bridge_previous_response_index.clear()
    for session in sessions:
        await service._close_http_bridge_session(session)
    for inflight_future in inflight_sessions:
        if not inflight_future.done():
            inflight_future.cancel()


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _collect_sse_events(
    async_client,
    path: str,
    *,
    json_body: dict,
    headers: dict[str, str] | None = None,
) -> list[dict]:
    async with async_client.stream("POST", path, json=json_body, headers=headers) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]
    return [
        event
        for line in lines
        if line[6:] != "[DONE]"
        if (event := json.loads(line[6:])).get("type") != "codex.keepalive"
    ]


async def _collect_sse_events_with_headers(
    async_client,
    path: str,
    *,
    json_body: dict,
    headers: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    async with async_client.stream("POST", path, json=json_body, headers=headers) as response:
        assert response.status_code == 200
        response_headers = dict(response.headers)
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]
    return [
        event
        for line in lines
        if line[6:] != "[DONE]"
        if (event := json.loads(line[6:])).get("type") != "codex.keepalive"
    ], response_headers


def _assert_created_text_delta_completed(events: list[dict]) -> None:
    assert [event["type"] for event in events] == [
        "response.created",
        "response.output_text.delta",
        "response.completed",
    ]
    assert events[1]["delta"] == "OK"


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return response.json()["accountId"]


async def _get_account(account_id: str) -> Account:
    async with SessionLocal() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one()
        session.expunge(account)
        return account


async def _wait_for_event(event: asyncio.Event, *, timeout: float = _TEST_SYNC_TIMEOUT_SECONDS) -> None:
    await asyncio.wait_for(event.wait(), timeout=timeout)


async def _replace_http_bridge_upstream_reader(
    service: proxy_module.ProxyService,
    session: proxy_module._HTTPBridgeSession,
    upstream: proxy_module.UpstreamResponsesWebSocket,
) -> None:
    reader = session.upstream_reader
    if reader is not None:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
    session.upstream = upstream
    session.closed = False
    session.upstream_control = proxy_module._WebSocketUpstreamControl()
    session.upstream_reader = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))


class _SettingsCache:
    def __init__(self, settings: DashboardSettings) -> None:
        self._settings = settings

    async def get(self) -> DashboardSettings:
        return self._settings


def _make_app_settings(
    *,
    enabled: bool,
    max_sessions: int = 128,
    queue_limit: int = 8,
    admission_wait_timeout_seconds: float = 0.05,
    codex_idle_ttl_seconds: float = 900.0,
    codex_prewarm_enabled: bool = False,
    instance_id: str = "instance-a",
    instance_ring: list[str] | None = None,
) -> Settings:
    return Settings(
        http_responses_session_bridge_enabled=enabled,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=codex_idle_ttl_seconds,
        http_responses_session_bridge_codex_prewarm_enabled=codex_prewarm_enabled,
        http_responses_session_bridge_max_sessions=max_sessions,
        http_responses_session_bridge_queue_limit=queue_limit,
        http_responses_session_bridge_instance_id=instance_id,
        http_responses_session_bridge_instance_ring=list(instance_ring or []),
        proxy_admission_wait_timeout_seconds=admission_wait_timeout_seconds,
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        openai_prompt_cache_key_derivation_enabled=True,
    )


def _make_dashboard_settings(
    *,
    prefer_earlier_reset_accounts: bool = False,
    gateway_safe_mode: bool = False,
    prompt_cache_idle_ttl_seconds: int | float = 3600,
) -> DashboardSettings:
    return DashboardSettings(
        id=1,
        sticky_threads_enabled=False,
        upstream_stream_transport="auto",
        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
        routing_strategy="usage_weighted",
        openai_cache_affinity_max_age_seconds=300,
        import_without_overwrite=False,
        totp_required_on_login=False,
        api_key_auth_enabled=False,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=int(prompt_cache_idle_ttl_seconds),
        http_responses_session_bridge_gateway_safe_mode=gateway_safe_mode,
        sticky_reallocation_budget_threshold_pct=95.0,
    )


def _install_proxy_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_settings: Settings,
    dashboard_settings: DashboardSettings,
) -> None:
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(dashboard_settings))
    monkeypatch.setattr(proxy_module, "get_settings", lambda: app_settings)


def _install_bridge_settings(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    _install_bridge_settings_with_limits(monkeypatch, enabled=enabled)


def _install_bridge_settings_with_limits(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
    max_sessions: int = 128,
    queue_limit: int = 8,
    admission_wait_timeout_seconds: float = 0.05,
    codex_idle_ttl_seconds: float = 900.0,
    prompt_cache_idle_ttl_seconds: float = 3600.0,
    codex_prewarm_enabled: bool = False,
    gateway_safe_mode: bool = False,
    prefer_earlier_reset_accounts: bool = False,
    instance_id: str = "instance-a",
    instance_ring: list[str] | None = None,
) -> None:
    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(
            enabled=enabled,
            max_sessions=max_sessions,
            queue_limit=queue_limit,
            admission_wait_timeout_seconds=admission_wait_timeout_seconds,
            codex_idle_ttl_seconds=codex_idle_ttl_seconds,
            codex_prewarm_enabled=codex_prewarm_enabled,
            instance_id=instance_id,
            instance_ring=instance_ring,
        ),
        dashboard_settings=_make_dashboard_settings(
            prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
            gateway_safe_mode=gateway_safe_mode,
            prompt_cache_idle_ttl_seconds=prompt_cache_idle_ttl_seconds,
        ),
    )


class _FakeUpstreamMessage:
    def __init__(
        self,
        kind: str,
        *,
        text: str | None = None,
        close_code: int | None = None,
        error: str | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.close_code = close_code
        self.error = error
        self.data = None


class _FakeBridgeUpstreamWebSocket:
    def __init__(self) -> None:
        self.sent_text: list[str] = []
        self.closed = False
        self._messages: asyncio.Queue[_FakeUpstreamMessage] = asyncio.Queue()

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_bridge_{len(self.sent_text)}"
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": response_id, "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            )
        )
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "OK"}],
                                }
                            ],
                            "usage": {
                                "input_tokens": 24,
                                "output_tokens": 2,
                                "total_tokens": 26,
                                "input_tokens_details": {"cached_tokens": 20},
                                "output_tokens_details": {"reasoning_tokens": 0},
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )

    async def send_bytes(self, data: bytes) -> None:
        raise AssertionError(f"Unexpected binary frame: {data!r}")

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    async def close(self) -> None:
        self.closed = True

    def response_header(self, name: str) -> str | None:
        del name
        return None


def _make_dummy_bridge_session(session_key: proxy_module._HTTPBridgeSessionKey) -> SimpleNamespace:
    async def _close() -> None:
        return None

    return SimpleNamespace(
        key=session_key,
        headers={},
        closed=False,
        account=SimpleNamespace(id=None, status=AccountStatus.ACTIVE),
        request_model="gpt-5.4",
        pending_lock=anyio.Lock(),
        pending_requests=deque(),
        queued_request_count=0,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
        codex_session=False,
        downstream_turn_state=None,
        downstream_turn_state_aliases=set(),
        previous_response_ids=set(),
        durable_session_id=None,
        durable_owner_epoch=None,
        upstream_reader=None,
        upstream_control=proxy_module._WebSocketUpstreamControl(),
        upstream=SimpleNamespace(close=_close),
    )


def _make_api_key_data(
    *,
    key_id: str,
    assigned_account_ids: list[str],
    account_assignment_scope_enabled: bool | None = None,
) -> proxy_module.ApiKeyData:
    return proxy_module.ApiKeyData(
        id=key_id,
        name="bridge-key",
        key_prefix="sk-bridge",
        allowed_models=None,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        last_used_at=None,
        account_assignment_scope_enabled=(
            bool(assigned_account_ids) if account_assignment_scope_enabled is None else account_assignment_scope_enabled
        ),
        assigned_account_ids=assigned_account_ids,
    )


@pytest.mark.asyncio
async def test_prepare_http_bridge_request_preserves_existing_client_metadata(app_instance):
    service = get_proxy_service_for_app(app_instance)
    payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "client_metadata": {
                "bool_flag": True,
                "count": 2,
                "nested": {"enabled": False},
            },
        }
    )

    token = set_request_id("req_http_bridge_existing")
    try:
        first_request_state, text_data = service._prepare_http_bridge_request(
            payload,
            {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
            api_key=None,
            api_key_reservation=None,
        )
        second_request_state, _ = service._prepare_http_bridge_request(
            payload,
            {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
            api_key=None,
            api_key_reservation=None,
        )
    finally:
        reset_request_id(token)

    assert json.loads(text_data)["client_metadata"] == {
        "bool_flag": True,
        "count": 2,
        "nested": {"enabled": False},
        "x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}',
    }
    assert first_request_state.request_log_id == "req_http_bridge_existing"
    assert second_request_state.request_log_id == "req_http_bridge_existing"
    assert first_request_state.request_id.startswith("ws_")
    assert second_request_state.request_id.startswith("ws_")
    assert first_request_state.request_id != second_request_state.request_id
