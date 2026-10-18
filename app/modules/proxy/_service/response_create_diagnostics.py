from __future__ import annotations

import gzip
import json
import logging
import re
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar, cast

from app.core.config.settings import DEFAULT_HOME_DIR, get_settings
from app.core.types import JsonValue
from app.modules.proxy._service.support import (
    _WebSocketRequestState,
)
from app.modules.proxy._service.support import (
    strip_encrypted_reasoning_from_request_text as strip_encrypted_reasoning_from_request_text,
)
from app.modules.proxy._service.support import (
    strip_encrypted_reasoning_input_items as strip_encrypted_reasoning_input_items,
)

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")

_OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS = 10
_OVERSIZED_RESPONSE_CREATE_DUMP_DIR: Path | None = None


def _service_module() -> Any | None:
    return sys.modules.get("app.modules.proxy.service")


def _service_global_or(name: str, fallback: T) -> T:
    service_module = _service_module()
    if service_module is None:
        return fallback
    return cast(T, getattr(service_module, name, fallback))


def _oversized_response_create_largest_items() -> int:
    return int(
        _service_global_or(
            "_OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS",
            _OVERSIZED_RESPONSE_CREATE_LARGEST_ITEMS,
        )
    )


def _oversized_response_create_dump_dir() -> Path:
    configured_dir = _service_global_or("_OVERSIZED_RESPONSE_CREATE_DUMP_DIR", _OVERSIZED_RESPONSE_CREATE_DUMP_DIR)
    if configured_dir is not None:
        return configured_dir
    settings_factory = _service_global_or("get_settings", get_settings)
    data_dir = getattr(settings_factory(), "data_dir", DEFAULT_HOME_DIR)
    return data_dir / "debug" / "response-create-dumps"


def _maybe_dump_oversized_response_create_request(
    request_state: _WebSocketRequestState,
    *,
    account_id_value: str | None,
    error_code: str,
    error_message: str | None,
) -> None:
    should_dump_oversized_response_create = _service_global_or(
        "_should_dump_oversized_response_create",
        _should_dump_oversized_response_create,
    )
    if not should_dump_oversized_response_create(error_code, error_message):
        return
    write_response_create_dump = _service_global_or("_write_response_create_dump", _write_response_create_dump)
    write_response_create_dump(
        request_state,
        account_id_value=account_id_value,
        error_code=error_code,
        error_message=error_message,
        log_prefix="oversized",
    )


def _write_response_create_dump(
    request_state: _WebSocketRequestState,
    *,
    account_id_value: str | None,
    error_code: str,
    error_message: str | None,
    log_prefix: str,
) -> bool:
    request_text = request_state.request_text
    if not request_text:
        return False

    payload_bytes = request_text.encode("utf-8")
    request_sha = sha256(payload_bytes).hexdigest()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    dump_id = "-".join(
        (
            timestamp,
            _safe_dump_slug(request_state.transport, fallback="transport"),
            _safe_dump_slug(request_state.model, fallback="model"),
            _safe_dump_slug(
                request_state.request_log_id or request_state.response_id or request_state.request_id,
                fallback="request",
            ),
        )
    )
    dump_dir = _oversized_response_create_dump_dir()
    dump_path = dump_dir / f"{dump_id}.response-create.json.gz"
    meta_path = dump_dir / f"{dump_id}.meta.json"

    meta: dict[str, JsonValue] = {
        "dump_id": dump_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "reason": {
            "error_code": error_code,
            "error_message": error_message,
        },
        "request": {
            "account_id": account_id_value,
            "request_id": request_state.request_id,
            "request_log_id": request_state.request_log_id,
            "response_id": request_state.response_id,
            "transport": request_state.transport,
            "model": request_state.model,
            "reasoning_effort": request_state.reasoning_effort,
            "service_tier": request_state.service_tier,
            "requested_service_tier": request_state.requested_service_tier,
            "actual_service_tier": request_state.actual_service_tier,
            "previous_response_id": request_state.previous_response_id,
            "awaiting_response_created": request_state.awaiting_response_created,
            "replay_count": request_state.replay_count,
            "request_text_bytes": len(payload_bytes),
            "request_text_chars": len(request_text),
            "request_text_sha256": request_sha,
        },
        "paths": {
            "dump_path": str(dump_path),
            "meta_path": str(meta_path),
        },
    }

    try:
        parsed_payload = json.loads(request_text)
    except json.JSONDecodeError as exc:
        meta["parse_error"] = str(exc)
    else:
        if isinstance(parsed_payload, dict):
            meta["summary"] = _summarize_response_create_payload(parsed_payload)
        else:
            meta["summary"] = {"payload_type": type(parsed_payload).__name__}

    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(dump_path, "wt", encoding="utf-8") as handle:
            handle.write(request_text)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        logger.exception(
            "Failed to dump %s response.create payload request_id=%s request_log_id=%s",
            log_prefix,
            request_state.request_id,
            request_state.request_log_id,
        )
        return False

    logger.warning(
        "Saved %s response.create dump request_id=%s request_log_id=%s dump_path=%s meta_path=%s bytes=%s",
        log_prefix,
        request_state.request_id,
        request_state.request_log_id,
        dump_path,
        meta_path,
        len(payload_bytes),
    )
    return True


def _should_dump_oversized_response_create(error_code: str, error_message: str | None) -> bool:
    if error_code != "stream_incomplete" or not error_message:
        return False
    normalized = error_message.lower()
    return "1009" in normalized or "message too big" in normalized


def _safe_dump_slug(value: str | None, *, fallback: str) -> str:
    if not value:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    if not normalized:
        return fallback
    return normalized[:80]


def _summarize_response_create_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    field_sizes = sorted(
        (
            {
                "key": key,
                "size_bytes": _json_size_bytes(value),
            }
            for key, value in payload.items()
        ),
        key=lambda item: int(item["size_bytes"]),
        reverse=True,
    )
    summary: dict[str, JsonValue] = {
        "top_level_keys": list(payload.keys()),
        "top_level_field_sizes": cast(JsonValue, field_sizes),
    }
    input_summary = _summarize_response_create_input(payload.get("input"))
    if input_summary is not None:
        summary["input"] = input_summary
    return summary


def _summarize_response_create_input(input_value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(input_value, list):
        return None

    input_items = cast(list[JsonValue], input_value)
    role_counts: dict[str, int] = {}
    item_type_counts: dict[str, int] = {}
    content_part_type_counts: dict[str, int] = {}
    largest_items: list[dict[str, JsonValue]] = []

    for index, item in enumerate(input_items):
        item_summary: dict[str, JsonValue] = {
            "index": index,
            "size_bytes": _json_size_bytes(item),
        }
        if isinstance(item, dict):
            item_object = cast(dict[str, JsonValue], item)
            role = item_object.get("role")
            if isinstance(role, str):
                item_summary["role"] = role
                role_counts[role] = role_counts.get(role, 0) + 1
            item_type = item_object.get("type")
            if isinstance(item_type, str):
                item_summary["type"] = item_type
                item_type_counts[item_type] = item_type_counts.get(item_type, 0) + 1
            content = item_object.get("content")
            if isinstance(content, list):
                item_summary["content_parts"] = len(content)
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_object = cast(dict[str, JsonValue], part)
                    part_type = part_object.get("type")
                    if isinstance(part_type, str):
                        content_part_type_counts[part_type] = content_part_type_counts.get(part_type, 0) + 1
        largest_items.append(item_summary)

    largest_items.sort(key=lambda item: cast(int, item["size_bytes"]), reverse=True)
    summary: dict[str, JsonValue] = {
        "count": len(input_value),
        "role_counts": cast(JsonValue, role_counts),
        "item_type_counts": cast(JsonValue, item_type_counts),
        "content_part_type_counts": cast(JsonValue, content_part_type_counts),
        "largest_items": cast(JsonValue, largest_items[: _oversized_response_create_largest_items()]),
    }
    return summary


def _json_size_bytes(value: JsonValue) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
