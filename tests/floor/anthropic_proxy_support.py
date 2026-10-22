from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from app.core.auth.dependencies import validate_proxy_api_key
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.db.session_models import StickySession, StickySessionKind
from app.db.usage_models import AdditionalUsageHistory
from app.modules.api_keys.service_data import ApiKeyData

ANTHROPIC_SSE_BYTES = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123","type":"message",'
    b'"role":"assistant","model":"claude-sonnet-4-20250514","content":[],"usage":{"input_tokens":10,'
    b'"cache_creation_input_tokens":3,"cache_read_input_tokens":4}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":5}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


def _override_proxy_api_key(app_instance, *, allowed_models: list[str]) -> ApiKeyData:
    api_key = ApiKeyData(
        id="planner-test-key",
        name="planner-test-key",
        key_prefix="sk-planner-test",
        allowed_models=allowed_models,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=utcnow(),
        last_used_at=None,
    )
    app_instance.dependency_overrides[validate_proxy_api_key] = lambda: api_key
    return api_key


class _FakeContent:
    def __init__(self, chunks: list[bytes], *, delay_before_chunk: dict[int, float] | None = None) -> None:
        self._chunks = chunks
        self._delay_before_chunk = delay_before_chunk or {}

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self._chunks):
            delay = self._delay_before_chunk.get(index)
            if delay:
                await asyncio.sleep(delay)
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes,
        headers: dict[str, str] | None = None,
        *,
        chunks: list[bytes] | None = None,
        delay_before_chunk: dict[int, float] | None = None,
    ) -> None:
        self.status = status
        body_chunks = chunks if chunks is not None else [body]
        self.content = _FakeContent(body_chunks, delay_before_chunk=delay_before_chunk)
        self._body = b"".join(body_chunks)
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body


class _FakeResponseContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


async def _insert_account(
    *,
    account_id: str,
    provider: str,
    access_token: str,
    email: str,
    status: AccountStatus = AccountStatus.ACTIVE,
    subscription_status: str | None = None,
) -> None:
    encryptor = TokenEncryptor()
    plan_type = {"anthropic": "max", "glm": "glm-coding", "kimi": "kimi-coding"}.get(provider, "plus")
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider=provider,
                chatgpt_account_id=f"workspace-{account_id}" if provider == "openai" else account_id,
                email=email,
                plan_type=plan_type,
                access_token_encrypted=encryptor.encrypt(access_token),
                refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                id_token_encrypted=encryptor.encrypt(f"id-{account_id}") if provider == "openai" else None,
                last_refresh=utcnow() + timedelta(days=1),
                status=status,
                deactivation_reason=None,
                subscription_status=subscription_status,
            )
        )
        await session.commit()


def _disable_pool_exhausted_wait(monkeypatch) -> None:
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", "false")
    get_settings.cache_clear()


async def _insert_quota_cooldown(
    *,
    account_id: str,
    quota_key: str,
    reset_at: int,
    window: str = "primary",
    recorded_at: datetime | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            AdditionalUsageHistory(
                account_id=account_id,
                quota_key=quota_key,
                limit_name=quota_key,
                metered_feature="anthropic_messages",
                window=window,
                used_percent=100.0,
                reset_at=reset_at,
                window_minutes=10,
                recorded_at=recorded_at or utcnow(),
            )
        )
        await session.commit()


@pytest.fixture
def fable_burn_first_enabled(monkeypatch):

    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_FABLE_BURN_FIRST_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def eastern_local_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


ANTHROPIC_JSON_BYTES = (
    b'{"id":"msg_456","type":"message","role":"assistant","model":"claude-sonnet-4-20250514",'
    b'"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn",'
    b'"usage":{"input_tokens":12,"output_tokens":7,"cache_creation_input_tokens":2,"cache_read_input_tokens":1}}'
)


async def _insert_weekly_usage(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None = None,
) -> None:
    from app.db.usage_models import UsageHistory

    effective_reset_at = reset_at or int((utcnow() + timedelta(days=5)).replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        session.add(
            UsageHistory(
                account_id=account_id,
                provider="anthropic",
                window="secondary",
                used_percent=used_percent,
                reset_at=effective_reset_at,
                window_minutes=10080,
                recorded_at=utcnow(),
            )
        )
        await session.commit()


async def _insert_fable_access_marker(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            AdditionalUsageHistory(
                account_id=account_id,
                quota_key="anthropic_fable_access",
                limit_name="anthropic_fable_access",
                metered_feature="anthropic_fable_probe",
                window="primary",
                used_percent=used_percent,
                reset_at=reset_at,
                window_minutes=None,
            )
        )
        await session.commit()


async def _insert_fable_scoped_weekly(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None = None,
    recorded_at: datetime | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            AdditionalUsageHistory(
                account_id=account_id,
                quota_key="anthropic_fable_scoped_weekly",
                limit_name="anthropic_fable_scoped_weekly",
                metered_feature="anthropic_fable_scoped_weekly",
                window="primary",
                used_percent=used_percent,
                reset_at=reset_at,
                window_minutes=10080,
                recorded_at=recorded_at or utcnow(),
            )
        )
        await session.commit()


async def _insert_primary_usage(
    *,
    account_id: str,
    used_percent: float,
    reset_at: int | None = None,
    credits_has: bool | None = None,
    credits_unlimited: bool | None = None,
    credits_balance: float | None = None,
) -> None:
    from app.db.usage_models import UsageHistory

    effective_reset = (
        reset_at
        if reset_at is not None
        else int((utcnow() + timedelta(hours=3)).replace(tzinfo=timezone.utc).timestamp())
    )
    async with SessionLocal() as session:
        session.add(
            UsageHistory(
                account_id=account_id,
                provider="anthropic",
                window="primary",
                used_percent=used_percent,
                reset_at=effective_reset,
                window_minutes=300,
                recorded_at=utcnow(),
                credits_has=credits_has,
                credits_unlimited=credits_unlimited,
                credits_balance=credits_balance,
            )
        )
        await session.commit()


_OVERAGE_BILLING_HEADERS = {
    "anthropic-ratelimit-unified-status": "rejected",
    "anthropic-ratelimit-unified-5h-status": "rejected",
    "anthropic-ratelimit-unified-5h-utilization": "1.0",
    "anthropic-ratelimit-unified-7d-status": "allowed",
    "anthropic-ratelimit-unified-overage-status": "allowed",
    "anthropic-ratelimit-unified-overage-in-use": "true",
}

_BILLED_BODY = b'{"text":"billed-extra-usage"}'

_ALLOWED_BODY = b'{"text":"subscription-covered"}'


class _BodyTrackingResponse(_FakeResponse):
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(status, body, headers)
        self.body_consumed = False
        response = self

        class _Content(_FakeContent):
            async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
                response.body_consumed = True
                async for chunk in super().iter_chunked(size):
                    yield chunk

        self.content = _Content([body])

    async def read(self) -> bytes:
        self.body_consumed = True
        return await super().read()


def _messages_payload() -> dict[str, object]:
    return {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "hello"}],
    }


async def _pin_sticky(account_id: str, payload: dict[str, object]) -> None:
    request = AnthropicMessageRequest.model_validate(payload)
    headers = {"anthropic-beta": "oauth-2025-04-20", "x-claude-session-id": f"session-{account_id}"}
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        headers,
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic"),
    )
    assert sticky_key is not None
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id=account_id,
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()


_OVERLOADED_529_BODY = b'{"error":{"type":"overloaded_error","message":"Overloaded"}}'


class _FailingConnectContext:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
