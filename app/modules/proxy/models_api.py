from __future__ import annotations

import time

from fastapi import Response
from fastapi.responses import JSONResponse

from app.core.openai.model_registry import get_model_registry, is_public_model
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.schemas import ModelListItem, ModelListResponse


async def _build_models_response(api_key: ApiKeyData | None) -> Response:
    from app.modules.proxy.api import (
        _allowed_models_for_api_key,
        _codex_model_entries,
        _codex_model_visibility_allowed_models,
        _enforce_request_limits,
        _release_reservation,
        _to_model_metadata,
        _v1_input_context_window,
        _v1_max_output_tokens,
        _v1_model_capabilities,
        _v1_supports_reasoning,
        _v1_supports_vision,
    )

    reservation = await _enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )

    allowed_models = _allowed_models_for_api_key(api_key)
    visibility_allowed_models = _codex_model_visibility_allowed_models(api_key)
    created = int(time.time())

    registry = get_model_registry()
    models = registry.get_models_with_fallback()

    items: list[ModelListItem] = [
        ModelListItem(id=model, created=created, owned_by="openrouter")
        for model in ("deepseek-flash", "deepseek-flash-floor")
        if allowed_models is None or model in allowed_models
    ]
    for slug, model in models.items():
        if not is_public_model(model, allowed_models):
            continue
        items.append(
            ModelListItem.model_validate(
                {
                    "id": slug,
                    "created": created,
                    "owned_by": "agent-lb",
                    "metadata": _to_model_metadata(model),
                    "api_types": ["chat_completions"],
                    "capabilities": _v1_model_capabilities(model),
                    "context_length": _v1_input_context_window(model),
                    "contextLength": _v1_input_context_window(model),
                    "max_output_tokens": _v1_max_output_tokens(model),
                    "maxOutputTokens": _v1_max_output_tokens(model),
                    "supports_reasoning": _v1_supports_reasoning(model),
                    "supportsReasoning": _v1_supports_reasoning(model),
                    "supports_images": _v1_supports_vision(model),
                    "supportsImages": _v1_supports_vision(model),
                    "supports_vision": _v1_supports_vision(model),
                    "supportsVision": _v1_supports_vision(model),
                }
            )
        )
    await _release_reservation(reservation)
    return JSONResponse(
        content=ModelListResponse(
            data=items,
            models=_codex_model_entries(models, allowed_models, visibility_allowed_models),
        ).model_dump(mode="json")
    )
