from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping
from hashlib import sha256
from typing import cast

import aiohttp
from anyio import CancelScope

from app.core.anthropic.models import AnthropicMessageRequest
from app.core.anthropic.quota_keys import model_quota_key
from app.core.config.settings import get_settings
from app.core.providers import get_anthropic_compat_profile, provider_name_for_anthropic_model
from app.core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    get_circuit_breaker_for_account,
)
from app.modules.accounts.credits import OPENROUTER_CREDITS_QUOTA_KEY, OPENROUTER_PROVIDER_NAME
from app.modules.proxy.anthropic_connect_retry import UpstreamStallTimeout
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.runtime_state import AccountLease
from app.modules.usage.additional_quota_keys import get_additional_quota_definition_for_model

CLIENT_STREAM_ERROR_TYPES = frozenset({"invalid_request_error", "not_found_error", "request_too_large"})
_NEUTRAL_CIRCUIT_STATUSES = frozenset({401, 403, 429})
_SSE_BLANK_LINE = re.compile(rb"(?>\r\n|\r|\n)(?>\r\n|\r|\n)")
ANTHROPIC_FAST_QUOTA_KEY = "anthropic_fast"
ANTHROPIC_PLANNER_MODEL_ALIAS = "claude-planner"
ANTHROPIC_PLANNER_MODEL = "claude-opus-5-5"


class UpstreamHttpStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Upstream HTTP {status_code}")


class UpstreamTruncatedEvent(UpstreamStallTimeout):
    def __init__(self) -> None:
        Exception.__init__(self, "Anthropic closed the stream inside an event (upstream_truncated_event)")
        self.phase = "truncated_event"
        self.seconds = 0.0
        self.code = "upstream_truncated_event"


class UpstreamBrokenStream(UpstreamStallTimeout):
    def __init__(self) -> None:
        Exception.__init__(self, "Anthropic stream broke before the response finished (upstream_stream_broken)")
        self.phase = "stream_broken"
        self.seconds = 0.0
        self.code = "upstream_stream_broken"


class StallCatcher:
    def __init__(self) -> None:
        self.stall: UpstreamStallTimeout | None = None

    async def __aenter__(self) -> StallCatcher:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if isinstance(exc, UpstreamStallTimeout):
            self.stall = exc
            return True
        return False


class AccountLeaseHolder:
    def __init__(self, load_balancer: LoadBalancer, lease: AccountLease | None = None) -> None:
        self._load_balancer = load_balancer
        self.lease = lease

    def set(self, lease: AccountLease | None) -> None:
        self.lease = lease

    async def release(self) -> None:
        lease = self.lease
        self.lease = None
        if lease is None:
            return
        with CancelScope(shield=True):
            await self._load_balancer.release_account_lease(lease)


class AnthropicCircuitGuard:
    def __init__(self, account_id: str, provider_name: str) -> None:
        self._account_id = account_id
        self._provider_name = provider_name
        self._cb: CircuitBreaker | None = None
        self._is_probe = False
        self._recorded = False

    async def enter(self) -> None:
        cb = get_circuit_breaker_for_account(self._account_id, get_settings(), provider=self._provider_name)
        self._cb = cb
        if cb is not None:
            self._is_probe = await cb.pre_call_check()

    async def record_success(self) -> None:
        if self._cb is None or self._recorded:
            return
        self._recorded = True
        await self._cb.record_success()

    async def record_failure(self, exc: Exception) -> None:
        if self._cb is None or self._recorded:
            return
        self._recorded = True
        await self._cb.record_failure(exc)

    async def record_http_status(self, status_code: int) -> None:
        if status_code >= 500:
            await self.record_failure(UpstreamHttpStatusError(status_code))
        elif status_code not in _NEUTRAL_CIRCUIT_STATUSES:
            await self.record_success()

    async def release(self) -> None:
        if not self._is_probe or self._cb is None:
            return
        self._is_probe = False
        with CancelScope(shield=True):
            await self._cb.release_half_open_probe()


def circuit_open_error(provider_label: str) -> AnthropicProxyError:
    return AnthropicProxyError(
        503,
        f"Circuit breaker open for {provider_label} account",
        code="upstream_circuit_open",
    )


def is_client_stream_error(error_type: str) -> bool:
    return error_type in CLIENT_STREAM_ERROR_TYPES


def sse_event_boundary(buffer: bytes | bytearray, start: int = 0) -> int:
    end = 0
    for match in _SSE_BLANK_LINE.finditer(buffer, max(0, start - 3)):
        end = match.end()
    return end


async def stall_bounded_chunks(
    chunks: AsyncIterator[bytes],
    *,
    first_byte_deadline: float | None,
    first_byte_seconds: float,
    idle_seconds: float,
) -> AsyncIterator[bytes]:
    iterator = chunks.__aiter__()
    loop = asyncio.get_running_loop()
    deadline = first_byte_deadline
    seen_bytes = False
    while True:
        bound = asyncio.timeout_at(deadline)
        try:
            async with bound:
                chunk = await anext(iterator)
        except StopAsyncIteration:
            return
        except aiohttp.ClientPayloadError as exc:
            raise UpstreamBrokenStream() from exc
        except TimeoutError:
            if not bound.expired():
                raise
            if seen_bytes:
                raise UpstreamStallTimeout("stream_idle", idle_seconds) from None
            raise UpstreamStallTimeout("first_byte", first_byte_seconds) from None
        if chunk:
            seen_bytes = True
            deadline = loop.time() + idle_seconds if idle_seconds > 0 else None
        yield chunk


async def close_async_iterator(iterator: object | None) -> None:
    if iterator is None:
        return
    aclose = getattr(iterator, "aclose", None)
    if aclose is None:
        return
    with CancelScope(shield=True):
        await aclose()


def frame_end_to_end_headers(headers: Mapping[str, str]) -> dict[str, str]:
    connection_tokens = {value.strip().lower() for value in headers.get("connection", "").split(",") if value.strip()}
    excluded = connection_tokens | {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


async def release_upstream_response(resp: object) -> None:
    release = getattr(resp, "release", None)
    if release is None:
        return
    released = release()
    if asyncio.iscoroutine(released) or asyncio.isfuture(released):
        await released


def anthropic_reasoning_effort(payload: AnthropicMessageRequest) -> str:
    output_config = (payload.model_extra or {}).get("output_config")
    effort = output_config.get("effort") if isinstance(output_config, dict) else None
    if isinstance(effort, str) and effort in {"low", "medium", "high", "xhigh", "max"}:
        return effort
    thinking = payload.thinking or {}
    budget = thinking.get("budget_tokens")
    if thinking.get("type") == "enabled" and isinstance(budget, int) and not isinstance(budget, bool):
        for threshold, band in ((4000, "low"), (16000, "medium"), (32000, "high")):
            if budget < threshold:
                return band
        return "xhigh"
    return "none"


def anthropic_session_header(headers: Mapping[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for key in (
        "x-claude-session-id",
        "x-claude-conversation-id",
        "x-claude-code-session-id",
        "session_id",
        "x-codex-session-id",
        "x-codex-conversation-id",
        "x-codex-turn-state",
    ):
        value = normalized.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def anthropic_content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        if isinstance(item, Mapping):
            value = cast(Mapping[str, object], item).get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts) if parts else None


def derive_anthropic_session_material(payload: AnthropicMessageRequest) -> str | None:
    parts: list[str] = [payload.model.lower()]
    system_text = anthropic_content_text(payload.system)
    if system_text:
        parts.append(system_text[:512])
    for message in payload.messages:
        if message.role == "user":
            user_text = anthropic_content_text(message.content)
            if user_text:
                parts.append(user_text[:512])
            break
    if len(parts) == 1:
        return None
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def hash_for_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:32]


def request_id_from_headers(headers: Mapping[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("request-id")


def anthropic_quota_key(payload: AnthropicMessageRequest) -> str:
    model = payload.model.lower()
    if "haiku" in model:
        return "anthropic_standard"
    return model_quota_key(payload.model, "anthropic_top_thinking" if payload.thinking else "anthropic_top")


def resolve_planner_model(model: str) -> str:
    if model.strip().lower() == ANTHROPIC_PLANNER_MODEL_ALIAS:
        return ANTHROPIC_PLANNER_MODEL
    return model


def is_fable_model(model: str | None) -> bool:
    return bool(model) and "fable" in model.lower()


def messages_provider_name(payload: AnthropicMessageRequest) -> str:
    return provider_name_for_model(payload.model)


def provider_name_for_model(model: str) -> str:
    definition = get_additional_quota_definition_for_model(model)
    if definition is not None and definition.quota_key == OPENROUTER_CREDITS_QUOTA_KEY:
        return OPENROUTER_PROVIDER_NAME
    return provider_name_for_anthropic_model(model)


def count_tokens_quota_key(provider_name: str) -> str:
    return get_anthropic_compat_profile(provider_name).count_tokens_quota_key


def anthropic_fast_mode_requested(payload: AnthropicMessageRequest) -> bool:
    speed = payload.speed
    return isinstance(speed, str) and speed.strip().lower() == "fast"


def messages_quota_key(payload: AnthropicMessageRequest, *, provider_name: str) -> str:
    profile = get_anthropic_compat_profile(provider_name)
    profile_quota_key = profile.quota_key(is_thinking=bool(payload.thinking))
    if profile_quota_key is not None:
        return profile_quota_key
    if anthropic_fast_mode_requested(payload):
        return ANTHROPIC_FAST_QUOTA_KEY
    return anthropic_quota_key(payload)


def messages_affinity_quota_key(payload: AnthropicMessageRequest, *, provider_name: str) -> str:
    profile = get_anthropic_compat_profile(provider_name)
    profile_quota_key = profile.quota_key(is_thinking=bool(payload.thinking))
    if profile_quota_key is not None:
        return profile_quota_key
    base = anthropic_quota_key(payload)
    if base == "anthropic_opus":
        base = "anthropic_top"
    elif base == "anthropic_opus_thinking":
        base = "anthropic_top_thinking"
    return f"{base}_fable" if is_fable_model(payload.model) else base


def sticky_prefix(provider_name: str) -> str:
    return get_anthropic_compat_profile(provider_name).sticky_prefix


def messages_sticky_key(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
    *,
    provider_name: str,
    quota_key: str,
) -> str | None:
    header_value = anthropic_session_header(headers)
    prefix = sticky_prefix(provider_name)
    if header_value:
        return f"{prefix}:{quota_key}:session:{hash_for_key(header_value)}"
    derived = derive_anthropic_session_material(payload)
    if not derived:
        return None
    return f"{prefix}:{quota_key}:derived:{hash_for_key(derived)}"


def anthropic_sticky_key(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
    *,
    quota_key: str,
) -> str | None:
    header_value = anthropic_session_header(headers)
    if header_value:
        return f"claude:{quota_key}:session:{hash_for_key(header_value)}"
    derived = derive_anthropic_session_material(payload)
    if not derived:
        return None
    return f"claude:{quota_key}:derived:{hash_for_key(derived)}"


def provider_label(provider_name: str) -> str:
    return get_anthropic_compat_profile(provider_name).label


def no_available_accounts_code(provider_name: str) -> str:
    return get_anthropic_compat_profile(provider_name).no_available_accounts_code


def quota_cooldown_code(provider_name: str) -> str:
    return get_anthropic_compat_profile(provider_name).quota_cooldown_code


def anthropic_request_session_id(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
) -> str | None:
    user_id = payload.metadata.get("user_id") if payload.metadata is not None else None
    if isinstance(user_id, str):
        try:
            decoded = json.loads(user_id)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            session_id = decoded.get("session_id")
            if isinstance(session_id, str) and session_id.strip():
                return session_id.strip()

        session_match = re.search(
            r"_session_([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
            user_id.strip(),
        )
        if session_match is not None:
            return session_match.group(1)

    return anthropic_session_header(headers)


__all__ = [
    "ANTHROPIC_FAST_QUOTA_KEY",
    "ANTHROPIC_PLANNER_MODEL",
    "ANTHROPIC_PLANNER_MODEL_ALIAS",
    "AccountLeaseHolder",
    "AnthropicCircuitGuard",
    "CLIENT_STREAM_ERROR_TYPES",
    "CircuitBreakerOpenError",
    "StallCatcher",
    "UpstreamBrokenStream",
    "UpstreamHttpStatusError",
    "UpstreamTruncatedEvent",
    "anthropic_content_text",
    "anthropic_fast_mode_requested",
    "anthropic_quota_key",
    "anthropic_reasoning_effort",
    "anthropic_request_session_id",
    "anthropic_session_header",
    "anthropic_sticky_key",
    "circuit_open_error",
    "close_async_iterator",
    "count_tokens_quota_key",
    "derive_anthropic_session_material",
    "frame_end_to_end_headers",
    "hash_for_key",
    "is_client_stream_error",
    "is_fable_model",
    "messages_affinity_quota_key",
    "messages_provider_name",
    "messages_quota_key",
    "messages_sticky_key",
    "no_available_accounts_code",
    "provider_label",
    "provider_name_for_model",
    "quota_cooldown_code",
    "release_upstream_response",
    "request_id_from_headers",
    "resolve_planner_model",
    "sse_event_boundary",
    "stall_bounded_chunks",
    "sticky_prefix",
]
