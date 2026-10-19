from __future__ import annotations

import logging
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar, cast

from app.core.clients.files import create_file as core_create_file
from app.core.clients.files import finalize_file as core_finalize_file
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.request_input import extract_input_file_ids, extract_input_image_file_references
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy._service.support import (
    _FilePinEntry,
)
from app.modules.proxy.affinity import (
    _is_synthesized_turn_state,
    _prompt_cache_key_from_request_model,
    _sticky_key_from_session_header,
    _sticky_key_from_turn_state_header,
)

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")
_ResponsesPayloadT = ResponsesRequest | ResponsesCompactRequest


_FAILED_ACCOUNT_ATTR = "_agent_lb_failed_account"
_REQUEST_TRANSPORT_HTTP = "http"


from app.modules.proxy._service.file_transfer import (
    _FileOpsServiceProtocol as _FileOpsServiceProtocol,
)
from app.modules.proxy._service.file_transfer import (
    _FileTransferMixin as _FileTransferMixin,
)


def _service_core_create_file() -> Callable[..., Awaitable[dict[str, JsonValue]]]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(
            Callable[..., Awaitable[dict[str, JsonValue]]],
            getattr(service_module, "core_create_file", core_create_file),
        )
    return core_create_file


def _service_core_finalize_file() -> Callable[..., Awaitable[dict[str, JsonValue]]]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(
            Callable[..., Awaitable[dict[str, JsonValue]]],
            getattr(service_module, "core_finalize_file", core_finalize_file),
        )
    return core_finalize_file


class _FileOpsMixin(_FileTransferMixin):
    _FILE_ACCOUNT_PIN_TTL_SECONDS: float = 30 * 60.0

    async def _pin_file_account(
        self,
        file_id: str,
        account_id: str,
    ) -> None:

        proxy = cast(_FileOpsServiceProtocol, self)
        if not file_id or not account_id:
            return
        expires_at = time.monotonic() + proxy._FILE_ACCOUNT_PIN_TTL_SECONDS
        async with proxy._file_account_pin_lock:
            proxy._file_account_pins[file_id] = _FilePinEntry(
                account_id=account_id,
                expires_at=expires_at,
            )
            proxy._evict_expired_file_pins_locked()

    async def _resolve_file_account(self, file_id: str) -> str | None:

        proxy = cast(_FileOpsServiceProtocol, self)
        entry = await proxy._lookup_file_pin(file_id)
        return entry.account_id if entry is not None else None

    async def _lookup_file_pin(self, file_id: str) -> _FilePinEntry | None:
        proxy = cast(_FileOpsServiceProtocol, self)
        if not file_id:
            return None
        async with proxy._file_account_pin_lock:
            proxy._evict_expired_file_pins_locked()
            entry = proxy._file_account_pins.get(file_id)
            if entry is None:
                return None
            if entry.expires_at <= time.monotonic():
                proxy._file_account_pins.pop(file_id, None)
                return None
            return entry

    def _evict_expired_file_pins_locked(self) -> None:

        proxy = cast(_FileOpsServiceProtocol, self)
        now = time.monotonic()
        expired = [file_id for file_id, entry in proxy._file_account_pins.items() if entry.expires_at <= now]
        for file_id in expired:
            proxy._file_account_pins.pop(file_id, None)

    async def _resolve_file_account_for_responses(
        self,
        payload: ResponsesRequest | ResponsesCompactRequest,
        headers: Mapping[str, str],
    ) -> str | None:

        proxy = cast(_FileOpsServiceProtocol, self)

        explicit_fields = getattr(payload, "model_fields_set", set())
        explicit_cache_key = "prompt_cache_key" in explicit_fields or "promptCacheKey" in explicit_fields
        if explicit_cache_key and _prompt_cache_key_from_request_model(payload) is not None:
            return None

        turn_state_value = _sticky_key_from_turn_state_header(headers)
        if turn_state_value is not None and not _is_synthesized_turn_state(turn_state_value):
            return None
        if _sticky_key_from_session_header(headers) is not None:
            return None
        if getattr(payload, "previous_response_id", None):
            return None

        file_ids = extract_input_file_ids(payload.input)
        if not file_ids:
            return None

        async with proxy._file_account_pin_lock:
            proxy._evict_expired_file_pins_locked()
            best_account: str | None = None
            best_expires_at = -1.0
            best_file_id: str | None = None
            for file_id in file_ids:
                entry = proxy._file_account_pins.get(file_id)
                if entry is None:
                    continue
                if entry.expires_at > best_expires_at or (
                    entry.expires_at == best_expires_at and (best_file_id is None or file_id < best_file_id)
                ):
                    best_account = entry.account_id
                    best_expires_at = entry.expires_at
                    best_file_id = file_id
            return best_account

    def _raise_for_unsupported_input_image_references(self, payload: _ResponsesPayloadT) -> None:
        references = extract_input_image_file_references(payload.input)
        if not references:
            return
        raise ProxyResponseError(
            400,
            openai_error(
                "unsupported_input_image_format",
                (
                    "input_image references via file_id or sediment:// URIs are not supported on "
                    "/v1/responses; the upstream API only accepts inline data: URLs. Send the "
                    "image inline (codex-cli style) or use the upload protocol exclusively for "
                    "MCP tool arguments."
                ),
            ),
        )

    async def create_file(
        self,
        payload: Mapping[str, JsonValue],
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:

        proxy = cast(_FileOpsServiceProtocol, self)
        result, account_id = await proxy._proxy_files_call(
            log_model="files-create",
            kind="files-create",
            api_key=api_key,
            headers=headers,
            invoke=lambda access_token, upstream_account_id, filtered_headers, route, route_trace: (
                _service_core_create_file()(
                    payload=payload,
                    headers=filtered_headers,
                    access_token=access_token,
                    account_id=upstream_account_id,
                    route=route,
                    allow_direct_egress=route is None,
                    route_trace=route_trace,
                )
            ),
        )

        if isinstance(result, dict) and account_id:
            file_id = result.get("file_id")
            if isinstance(file_id, str) and file_id:
                await proxy._pin_file_account(file_id, account_id)
        return result

    async def finalize_file(
        self,
        file_id: str,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None = None,
    ) -> dict[str, JsonValue]:

        proxy = cast(_FileOpsServiceProtocol, self)
        pinned_account_id = await proxy._resolve_file_account(file_id)
        result, account_id = await proxy._proxy_files_call(
            log_model="files-finalize",
            kind="files-finalize",
            api_key=api_key,
            headers=headers,
            preferred_account_id=pinned_account_id,
            invoke=lambda access_token, upstream_account_id, filtered_headers, route, route_trace: (
                _service_core_finalize_file()(
                    file_id=file_id,
                    headers=filtered_headers,
                    access_token=access_token,
                    account_id=upstream_account_id,
                    route=route,
                    allow_direct_egress=route is None,
                    route_trace=route_trace,
                )
            ),
        )
        if isinstance(result, dict) and account_id:
            status = result.get("status")
            if status == "success":
                await proxy._pin_file_account(file_id, account_id)
        return result
