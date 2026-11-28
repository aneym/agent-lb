from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.anthropic.response_observability import is_model_mismatch as _anthropic_is_model_mismatch
from app.core.model_aliases import requested_model_alias
from app.core.openai.models import CompactResponsePayload, OpenAIResponse, OpenAIResponsePayload

logger = logging.getLogger(__name__)

_ISO_DATE_SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NON_TERMINAL_STATUSES = frozenset({"in_progress", "queued"})


@dataclass(frozen=True, slots=True)
class OpenAIResponseMetadata:
    response_model: str | None = None
    stop_reason: str | None = None
    stop_details_category: str | None = None


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _has_refusal_output(output: Any) -> bool:
    if not isinstance(output, list):
        return False
    for item in output:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type == "refusal":
            return True
        if item_type != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "refusal":
                return True
    return False


def is_model_mismatch(requested_model: str, response_model: str | None) -> bool:
    if not _anthropic_is_model_mismatch(requested_model, response_model):
        return False
    requested = requested_model.strip().lower()
    responded = response_model.strip().lower() if isinstance(response_model, str) else ""
    if not requested or not responded:
        return False
    if requested.endswith("-latest"):
        base = requested[: -len("-latest")]
        if (
            base
            and responded.startswith(f"{base}-")
            and _ISO_DATE_SNAPSHOT_RE.fullmatch(responded[len(base) + 1 :]) is not None
        ):
            return False
    if _ISO_DATE_SNAPSHOT_RE.search(requested) is None:
        if (
            responded.startswith(f"{requested}-")
            and _ISO_DATE_SNAPSHOT_RE.fullmatch(responded[len(requested) + 1 :]) is not None
        ):
            return False
    return True


def extract_openai_response_metadata(
    response: OpenAIResponse | OpenAIResponsePayload | CompactResponsePayload | None,
    *,
    raw_payload: Mapping[str, Any] | None = None,
    current: OpenAIResponseMetadata | None = None,
) -> OpenAIResponseMetadata:
    prior = current or OpenAIResponseMetadata()
    raw_response_map: Mapping[str, Any] | None = None
    if isinstance(raw_payload, Mapping):
        nested = raw_payload.get("response")
        if isinstance(nested, Mapping):
            raw_response_map = nested

    extracted_model = _clean_str(response.model) if response is not None else None
    if extracted_model is None and raw_response_map is not None:
        extracted_model = _clean_str(raw_response_map.get("model"))
    if extracted_model is None and isinstance(raw_payload, Mapping):
        extracted_model = _clean_str(raw_payload.get("model"))
    response_model = extracted_model or prior.response_model

    incomplete_reason = (
        _clean_str(response.incomplete_details.reason)
        if response is not None and response.incomplete_details is not None
        else None
    )
    if incomplete_reason is None and raw_response_map is not None:
        raw_incomplete = raw_response_map.get("incomplete_details")
        if isinstance(raw_incomplete, Mapping):
            incomplete_reason = _clean_str(raw_incomplete.get("reason"))
    if incomplete_reason is None and isinstance(raw_payload, Mapping):
        raw_incomplete = raw_payload.get("incomplete_details")
        if isinstance(raw_incomplete, Mapping):
            incomplete_reason = _clean_str(raw_incomplete.get("reason"))

    raw_output: Any = None
    if raw_response_map is not None and "output" in raw_response_map:
        raw_output = raw_response_map.get("output")
    elif isinstance(raw_payload, Mapping) and "output" in raw_payload:
        raw_output = raw_payload.get("output")
    elif response is not None:
        raw_output = getattr(response, "output", None)
    refusal_detected = _has_refusal_output(raw_output)

    status = _clean_str(response.status) if response is not None else None
    if status is None and raw_response_map is not None:
        status = _clean_str(raw_response_map.get("status"))
    if status is None and isinstance(raw_payload, Mapping):
        status = _clean_str(raw_payload.get("status"))
    if status is not None and status.lower() in _NON_TERMINAL_STATUSES:
        status = None

    if incomplete_reason is not None:
        stop_reason: str | None = incomplete_reason
    elif refusal_detected:
        stop_reason = "refusal"
    elif status is not None:
        stop_reason = status
    else:
        stop_reason = prior.stop_reason

    if stop_reason is not None and stop_reason.lower() == "content_filter":
        stop_details_category: str | None = "content_filter"
    elif refusal_detected or (stop_reason is not None and stop_reason.lower() == "refusal"):
        stop_details_category = "refusal"
    else:
        stop_details_category = prior.stop_details_category

    return OpenAIResponseMetadata(
        response_model=response_model,
        stop_reason=stop_reason,
        stop_details_category=stop_details_category,
    )


def observe_openai_response(
    *,
    request_id: str,
    account_id: str | None,
    requested_model: str | None,
    metadata: OpenAIResponseMetadata,
) -> None:
    response_model = metadata.response_model
    if requested_model and response_model is not None and is_model_mismatch(requested_model, response_model):
        alias = requested_model_alias.get()
        logger.warning(
            "openai_response_model_mismatch request_id=%s account_id=%s "
            "requested_model=%s requested_alias=%s response_model=%s",
            request_id,
            account_id or "-",
            requested_model,
            alias or "-",
            response_model,
        )
    normalized_stop_reason = metadata.stop_reason.strip().lower() if isinstance(metadata.stop_reason, str) else None
    if normalized_stop_reason in {"refusal", "content_filter"} or metadata.stop_details_category is not None:
        logger.warning(
            "openai_upstream_refusal request_id=%s account_id=%s "
            "requested_model=%s response_model=%s stop_reason=%s stop_details_category=%s",
            request_id,
            account_id or "-",
            requested_model or "-",
            response_model or "-",
            metadata.stop_reason or "-",
            metadata.stop_details_category or "unknown",
        )
