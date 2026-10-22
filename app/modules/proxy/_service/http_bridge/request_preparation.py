from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping, cast
from uuid import uuid4

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.identity import get_request_identity
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy._service.http_bridge.service_stubs import (
    _count_external_image_urls,
    _enforce_response_create_size_limit,
    _fingerprint_input_items,
    _inline_top_level_input_image_urls,
    _normalize_service_tier_value,
    _normalize_session_id,
    _response_create_client_metadata,
    _service_as_image_fetch_session,
    _service_get_settings,
    _service_inline_input_image_urls,
    _service_lease_http_session,
    _service_time,
    _slim_response_create_payload_for_upstream,
    _upstream_response_create_max_bytes,
)
from app.modules.proxy._service.support import (
    _request_log_useragent_fields,
    _WebSocketRequestState,
    request_input_contains_compaction_trigger,
    strip_encrypted_reasoning_input_items,
)
from app.modules.proxy.affinity import _owner_lookup_session_id_from_headers
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.tool_call_dedupe import dedupe_replayed_side_effect_input_items

logger = logging.getLogger("app.modules.proxy.service")

_REQUEST_TRANSPORT_HTTP = "http"



import logging

from app.core.openai.parsing import parse_sse_event
from app.core.resilience.overload import is_local_overload_error_code
from app.core.utils.sse import parse_sse_data_json
from app.modules.proxy._service.http_bridge.helpers import _build_http_bridge_prewarm_text, _log_http_bridge_event
from app.modules.proxy._service.http_bridge.service_stubs import (
    _prewarm_response_timeout_seconds,
    _release_websocket_response_create_gate,
)
from app.modules.proxy._service.support import _event_type_from_payload, _HTTPBridgeSession
from app.modules.proxy.affinity import _extract_model_class
from app.modules.proxy.helpers import _normalize_error_code, _parse_openai_error


class HTTPBridgeRequestPreparationMixin:
    def _prepare_http_bridge_request(
        self: Any,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        request_id: str | None = None,
    ) -> tuple[_WebSocketRequestState, str]:
        request_state, text_data = self._prepare_response_bridge_request_state(
            payload,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            include_type_field=True,
            attach_event_queue=True,
            transport=_REQUEST_TRANSPORT_HTTP,
            client_metadata=_response_create_client_metadata(payload.to_payload(), headers=headers),
            session_id=_owner_lookup_session_id_from_headers(headers),
            request_log_id=request_id or get_request_id() or ensure_request_id(None),
        )
        request_state.useragent, request_state.useragent_group = _request_log_useragent_fields(headers)
        return request_state, text_data


    def _prepare_response_bridge_request_state(
        self: Any,
        payload: ResponsesRequest,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        include_type_field: bool,
        attach_event_queue: bool,
        transport: str,
        client_metadata: Mapping[str, JsonValue] | None,
        session_id: str | None = None,
        request_id: str | None = None,
        request_log_id: str | None = None,
    ) -> tuple[_WebSocketRequestState, str]:
        stripped_reasoning_count = 0
        dropped_reasoning_count = 0
        if (
            payload.previous_response_id is None
            and isinstance(payload.input, list)
            and getattr(_service_get_settings(), "http_bridge_strip_foreign_encrypted_reasoning", True)
        ):
            unsanitized_input_items = cast(list[JsonValue], payload.input)
            sanitized_input_items, stripped_reasoning_count, dropped_reasoning_count = (
                strip_encrypted_reasoning_input_items(unsanitized_input_items)
            )
            if stripped_reasoning_count or dropped_reasoning_count:
                payload = payload.model_copy(update={"input": sanitized_input_items})
        deduped_replayed_input_count: int | None = None
        deduped_replayed_input_fingerprint: str | None = None
        deduped_replayed_tool_call_count = 0
        if payload.previous_response_id is not None and isinstance(payload.input, list):
            replayed_input_items = cast(list[JsonValue], payload.input)
            deduped_input_items, deduped_replayed_tool_call_count = dedupe_replayed_side_effect_input_items(
                replayed_input_items,
                sanitize_missing_outputs=False,
            )
            if deduped_replayed_tool_call_count > 0:
                deduped_replayed_input_count = len(replayed_input_items)
                deduped_replayed_input_fingerprint = _fingerprint_input_items(replayed_input_items)
                payload = payload.model_copy(update={"input": deduped_input_items})
        upstream_payload = dict(payload.to_payload())
        upstream_payload.pop("stream", None)
        upstream_payload.pop("background", None)
        if include_type_field:
            upstream_payload["type"] = "response.create"
        if client_metadata:
            upstream_payload["client_metadata"] = client_metadata
        forwarded_service_tier = _normalize_service_tier_value(upstream_payload.get("service_tier"))
        input_item_count = 0
        input_full_fingerprint: str | None = None
        payload_input = payload.input
        if isinstance(payload_input, list):
            payload_input_list = cast(list[JsonValue], payload_input)
            input_item_count = len(payload_input_list)
            if input_item_count > 0:
                input_full_fingerprint = _fingerprint_input_items(payload_input_list)

        request_state = _WebSocketRequestState(
            request_id=request_id or f"ws_{uuid4().hex}",
            request_log_id=request_log_id,
            model=payload.model,
            service_tier=forwarded_service_tier,
            reasoning_effort=payload.reasoning.effort if payload.reasoning else None,
            api_key_reservation=api_key_reservation,
            started_at=_service_time().monotonic(),
            requested_service_tier=forwarded_service_tier,
            awaiting_response_created=True,
            event_queue=asyncio.Queue() if attach_event_queue else None,
            transport=transport,
            api_key=api_key,
            request_usage_budget=estimate_api_key_request_usage(payload),
            previous_response_id=payload.previous_response_id,
            session_id=_normalize_session_id(session_id),
            input_item_count=input_item_count,
            input_full_fingerprint=input_full_fingerprint,
            is_compaction_request=request_input_contains_compaction_trigger(payload.input),
            identity=get_request_identity(),
        )
        if deduped_replayed_input_count is not None:
            request_state.input_item_count = deduped_replayed_input_count
            request_state.input_full_fingerprint = deduped_replayed_input_fingerprint
            logger.warning(
                "%s_replayed_tool_call_input_deduped request_id=%s original_items=%s deduped_to=%s "
                "removed_tool_calls=%s previous_response_id=%s",
                transport,
                request_state.request_id,
                deduped_replayed_input_count,
                input_item_count,
                deduped_replayed_tool_call_count,
                payload.previous_response_id,
            )
        if stripped_reasoning_count or dropped_reasoning_count:
            logger.warning(
                "%s_encrypted_reasoning_stripped request_id=%s request_log_id=%s "
                "dropped_blob_items=%s dropped_plain_items=%s remaining_items=%s",
                transport,
                request_state.request_id,
                request_state.request_log_id,
                stripped_reasoning_count,
                dropped_reasoning_count,
                input_item_count,
            )
        text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
        payload_size = len(text_data.encode("utf-8"))
        max_bytes = _upstream_response_create_max_bytes()
        if payload_size > max_bytes:
            slimmed_payload, slim_summary = _slim_response_create_payload_for_upstream(
                upstream_payload,
                max_bytes=max_bytes,
            )
            if slim_summary is not None:
                upstream_payload = slimmed_payload
                text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
                logger.warning(
                    (
                        "Slimmed response.create request_id=%s request_log_id=%s transport=%s "
                        "original_bytes=%s slimmed_bytes=%s "
                        "historical_tool_outputs_slimmed=%s historical_images_slimmed=%s"
                    ),
                    request_state.request_id,
                    request_state.request_log_id,
                    transport,
                    payload_size,
                    len(text_data.encode("utf-8")),
                    slim_summary["historical_tool_outputs_slimmed"],
                    slim_summary["historical_images_slimmed"],
                )
        request_state.request_text = text_data
        _enforce_response_create_size_limit(request_state)
        return request_state, text_data


    async def _inline_http_bridge_image_urls(
        self: Any,
        text_data: str,
        request_state: _WebSocketRequestState,
    ) -> str:

        settings = _service_get_settings()
        if not settings.image_inline_fetch_enabled:
            return text_data

        if "input_image" not in text_data:
            return text_data
        try:
            payload_dict: dict[str, JsonValue] = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            return text_data
        connect_timeout = getattr(settings, "upstream_connect_timeout_seconds", 5.0)
        async with _service_lease_http_session()() as http_session:
            image_fetch_session = _service_as_image_fetch_session()(http_session)
            inlined = await _service_inline_input_image_urls()(
                payload_dict,
                image_fetch_session,
                connect_timeout,
            )
            inlined = await _inline_top_level_input_image_urls(inlined, image_fetch_session, connect_timeout)

        remaining_external = _count_external_image_urls(inlined)
        if remaining_external > 0:
            raise ProxyResponseError(
                400,
                openai_error(
                    "image_download_failed",
                    (
                        f"Failed to download {remaining_external} external image(s). "
                        "The upstream API only accepts inline data: URLs. "
                        "Send images as base64 data URLs (data:image/png;base64,...) "
                        "or ensure the image URLs are publicly accessible."
                    ),
                ),
            )
        updated_text = json.dumps(inlined, ensure_ascii=True, separators=(",", ":"))
        if updated_text == text_data:
            return text_data
        request_state.request_text = updated_text
        _enforce_response_create_size_limit(request_state)
        return updated_text

    async def _maybe_prewarm_http_bridge_session(
        self: Any,
        session: "_HTTPBridgeSession",
        *,
        request_state: _WebSocketRequestState,
        text_data: str,
    ) -> None:
        if (
            not session.codex_session
            or session.prewarmed
            or request_state.previous_response_id is not None
            or not getattr(_service_get_settings(), "http_responses_session_bridge_codex_prewarm_enabled", False)
        ):
            return
        prewarm_lock = session.prewarm_lock
        if prewarm_lock is None:
            return
        async with prewarm_lock:
            if session.prewarmed:
                return
            warmup_text = _build_http_bridge_prewarm_text(text_data)
            session.prewarmed = True
            if warmup_text is None:
                return

            warmup_state = _WebSocketRequestState(
                request_id=f"http_prewarm_{uuid4().hex}",
                model=request_state.model,
                service_tier=request_state.service_tier,
                reasoning_effort=request_state.reasoning_effort,
                api_key_reservation=None,
                started_at=_service_time().monotonic(),
                requested_service_tier=request_state.requested_service_tier,
                actual_service_tier=request_state.actual_service_tier,
                awaiting_response_created=True,
                event_queue=asyncio.Queue(),
                transport=_REQUEST_TRANSPORT_HTTP,
                request_text=warmup_text,
                skip_request_log=True,
            )
            gate_acquired = False
            request_enqueued = False
            try:
                event_queue = warmup_state.event_queue
                assert event_queue is not None
                await self._acquire_request_state_response_create_admission(
                    warmup_state,
                    response_create_gate=session.response_create_gate,
                    account_id=session.account.id,
                    surface="http_bridge_prewarm",
                    bridge_session=session,
                )
                gate_acquired = True
                async with session.lifecycle_lock:
                    current_session = session
                    http_bridge_sessions = getattr(self, "_http_bridge_sessions", None)
                    bridge_lock = getattr(self, "_http_bridge_lock", None)
                    if bridge_lock is not None:
                        async with bridge_lock:
                            if http_bridge_sessions is not None:
                                current_session = http_bridge_sessions.get(session.key)
                    elif http_bridge_sessions is not None:
                        current_session = http_bridge_sessions.get(session.key)
                    session_replaced = current_session is not session
                    if session.closed or session_replaced:
                        _log_http_bridge_event(
                            "submit_on_closed",
                            session.key,
                            account_id=session.account.id,
                            model=session.request_model,
                            detail=(
                                "prewarm_session_retired_after_admission"
                                if session.closed
                                else "prewarm_session_replaced_after_admission"
                            ),
                            cache_key_family=session.key.affinity_kind,
                            model_class=_extract_model_class(session.request_model) if session.request_model else None,
                        )
                        session.prewarmed = False
                        await self._cleanup_http_bridge_submit_interruption(
                            session,
                            request_state=warmup_state,
                            gate_acquired=gate_acquired,
                            request_enqueued=request_enqueued,
                            counted_in_queue=False,
                        )
                        gate_acquired = False
                        return
                    async with session.pending_lock:
                        session.pending_requests.append(warmup_state)
                    request_enqueued = True
                    await session.upstream.send_text(warmup_text)
                while True:
                    try:
                        event_block = await asyncio.wait_for(
                            event_queue.get(),
                            timeout=_prewarm_response_timeout_seconds(),
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "HTTP bridge prewarm timed out request_id=%s model=%s",
                            request_state.request_id,
                            request_state.model,
                        )
                        session.prewarmed = False
                        try:
                            await self._reconnect_http_bridge_session(
                                session,
                                request_state=request_state,
                                restart_reader=True,
                            )
                        except Exception:
                            session.closed = True
                            raise
                        finally:
                            async with session.pending_lock:
                                if warmup_state in session.pending_requests:
                                    session.pending_requests.remove(warmup_state)
                            self._cancel_request_state_api_key_reservation_heartbeat(warmup_state)
                            if gate_acquired:
                                await _release_websocket_response_create_gate(
                                    warmup_state,
                                    session.response_create_gate,
                                )
                        return
                    if event_block is None:
                        break
                    payload = parse_sse_data_json(event_block)
                    event = parse_sse_event(event_block)
                    event_type = _event_type_from_payload(event, payload)
                    if event_type in {"response.failed", "response.incomplete", "error"}:
                        raise ProxyResponseError(
                            502,
                            openai_error(
                                "upstream_unavailable",
                                "HTTP responses session bridge prewarm failed",
                            ),
                        )
                session.last_used_at = _service_time().monotonic()
            except ProxyResponseError as exc:
                error = _parse_openai_error(exc.payload)
                code = _normalize_error_code(error.code if error else None, error.type if error else None)
                await self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=warmup_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                    counted_in_queue=False,
                )
                if is_local_overload_error_code(code):
                    session.prewarmed = False
                    return
                session.prewarmed = False
                raise
            except BaseException:
                session.prewarmed = False
                await self._cleanup_http_bridge_submit_interruption(
                    session,
                    request_state=warmup_state,
                    gate_acquired=gate_acquired,
                    request_enqueued=request_enqueued,
                    counted_in_queue=False,
                )
                raise
