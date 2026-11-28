from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from app.core.anthropic.parsing import AnthropicResponseMetadata
from app.core.metrics.prometheus import anthropic_model_mismatches_total, anthropic_refusals_total
from app.core.model_aliases import requested_model_alias

logger = logging.getLogger(__name__)

BILLED_REFUSAL_CATEGORIES = frozenset({"frontier_llm", "reasoning_extraction", "bio"})
_CONTEXT_SUFFIX_RE = re.compile(r"\[[^\]]+\]$")
_DATED_SNAPSHOT_RE = re.compile(r"^\d{8}$")
_WHITESPACE_RE = re.compile(r"[\x00-\x1f\x7f\s]+")


def upstream_requested_model(body_payload: Mapping[str, Any], fallback_model: str) -> str:
    model = body_payload.get("model")
    return model if isinstance(model, str) and model.strip() else fallback_model


def _normalize_model_name(model: str | None) -> str:
    if not isinstance(model, str):
        return ""
    return _CONTEXT_SUFFIX_RE.sub("", model.strip().lower())


def is_model_mismatch(requested_model: str, response_model: str | None) -> bool:
    requested = _normalize_model_name(requested_model)
    responded = _normalize_model_name(response_model)
    if not requested or not responded or responded == requested:
        return False
    if requested.endswith("-latest"):
        base = requested[: -len("-latest")]
        if base and (
            responded == base
            or (
                responded.startswith(f"{base}-")
                and _DATED_SNAPSHOT_RE.fullmatch(responded[len(base) + 1 :]) is not None
            )
        ):
            return False
    if _DATED_SNAPSHOT_RE.fullmatch(requested.rsplit("-", 1)[-1]) is None:
        if (
            responded.startswith(f"{requested}-")
            and _DATED_SNAPSHOT_RE.fullmatch(responded[len(requested) + 1 :]) is not None
        ):
            return False
    return True


def is_billed_refusal_category(category: str | None) -> bool:
    return bool(category) and category.strip().lower() in BILLED_REFUSAL_CATEGORIES


def sanitize_stop_explanation(value: str | None, *, max_length: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_length:
        return cleaned
    return f"{cleaned[: max_length - 3]}..."


def observe_anthropic_response(
    *,
    request_id: str,
    account_id: str | None,
    provider_name: str,
    requested_model: str,
    metadata: AnthropicResponseMetadata,
    requested_alias: str | None = None,
) -> None:
    alias = requested_alias if requested_alias is not None else requested_model_alias.get()
    response_model = metadata.response_model
    if response_model is not None and is_model_mismatch(requested_model, response_model):
        logger.warning(
            "anthropic_response_model_mismatch request_id=%s account_id=%s provider=%s "
            "requested_model=%s requested_alias=%s response_model=%s",
            request_id,
            account_id or "-",
            provider_name,
            requested_model,
            alias or "-",
            response_model,
        )
        if anthropic_model_mismatches_total is not None:
            anthropic_model_mismatches_total.labels(
                requested_model=requested_model,
                response_model=response_model,
            ).inc()
    if metadata.stop_reason is not None and metadata.stop_reason.strip().lower() == "refusal":
        category = metadata.stop_details_category or "unknown"
        billed = is_billed_refusal_category(metadata.stop_details_category)
        explanation = sanitize_stop_explanation(metadata.stop_details_explanation)
        logger.warning(
            "anthropic_upstream_refusal request_id=%s account_id=%s provider=%s "
            "requested_model=%s response_model=%s stop_reason=%s stop_details_category=%s "
            "billed_refusal=%s stop_details_explanation=%s",
            request_id,
            account_id or "-",
            provider_name,
            requested_model,
            response_model or "-",
            metadata.stop_reason,
            category,
            "true" if billed else "false",
            explanation or "-",
        )
        if anthropic_refusals_total is not None:
            anthropic_refusals_total.labels(
                requested_model=requested_model,
                response_model=response_model or "unknown",
                category=category,
                billed="true" if billed else "false",
            ).inc()
