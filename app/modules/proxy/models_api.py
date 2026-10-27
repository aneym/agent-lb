from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Response, Security
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_openai_error_format, validate_proxy_api_key
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.model_aliases import model_is_visible
from app.core.openai.model_registry import get_model_registry, is_public_model
from app.core.providers.anthropic_compat import ANTHROPIC_COMPAT_PROFILES
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.schemas import ModelListItem, ModelListResponse

router = APIRouter(
    prefix="/v1",
    tags=["proxy"],
    dependencies=[Depends(set_openai_error_format), Security(validate_proxy_api_key)],
)


@router.get("/models/{model_id:path}", response_model=ModelListItem)
async def retrieve_model(
    model_id: str,
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    catalog = await build_models_catalog(api_key)
    for model in catalog.data:
        if model.id == model_id:
            return JSONResponse(content=model.model_dump(mode="json"))
    return JSONResponse(
        status_code=404,
        content=openai_error(
            "model_not_found",
            f"The model '{model_id}' does not exist or you do not have access to it.",
            error_type="invalid_request_error",
        ),
    )


async def build_models_response(api_key: ApiKeyData | None) -> Response:
    catalog = await build_models_catalog(api_key)
    return JSONResponse(content=catalog.model_dump(mode="json"))


async def build_models_catalog(api_key: ApiKeyData | None) -> ModelListResponse:
    from app.modules.proxy.model_metadata import (
        allowed_models_for_api_key,
        codex_model_entries,
        codex_model_visibility_allowed_models,
        to_model_metadata,
        v1_input_context_window,
        v1_max_output_tokens,
        v1_model_capabilities,
        v1_supports_reasoning,
        v1_supports_vision,
    )
    from app.modules.proxy.request_admission import enforce_request_limits
    from app.modules.proxy.reservations import release_reservation

    reservation = await enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )

    allowed_models = allowed_models_for_api_key(api_key)
    visibility_allowed_models = codex_model_visibility_allowed_models(api_key)
    created = int(time.time())

    registry = get_model_registry()
    models = registry.get_models_with_fallback()

    items: list[ModelListItem] = [
        ModelListItem(id=model, created=created, owned_by="openrouter")
        for model in ("deepseek-flash", "deepseek-flash-floor")
        if allowed_models is None or model in allowed_models
    ]
    items.extend(
        ModelListItem(id=model, created=created, owned_by=profile.provider_name)
        for profile in ANTHROPIC_COMPAT_PROFILES
        for model in profile.listed_models
        if allowed_models is None or model in allowed_models
    )
    for slug, model in models.items():
        if not is_public_model(model, allowed_models):
            continue
        items.append(
            ModelListItem.model_validate(
                {
                    "id": slug,
                    "created": created,
                    "owned_by": "agent-lb",
                    "metadata": to_model_metadata(model),
                    "api_types": ["chat_completions"],
                    "capabilities": v1_model_capabilities(model),
                    "context_length": v1_input_context_window(model),
                    "contextLength": v1_input_context_window(model),
                    "max_output_tokens": v1_max_output_tokens(model),
                    "maxOutputTokens": v1_max_output_tokens(model),
                    "supports_reasoning": v1_supports_reasoning(model),
                    "supportsReasoning": v1_supports_reasoning(model),
                    "supports_images": v1_supports_vision(model),
                    "supportsImages": v1_supports_vision(model),
                    "supports_vision": v1_supports_vision(model),
                    "supportsVision": v1_supports_vision(model),
                }
            )
        )
    items = [item for item in items if model_is_visible(item.id)]
    by_id = {item.id: item for item in items}
    for alias, target in get_settings().model_aliases.items():
        if not model_is_visible(alias) or (allowed_models is not None and target not in allowed_models):
            continue
        if alias in by_id:
            items = [item for item in items if item.id != alias]
        entry = by_id.get(target, ModelListItem(id=target, created=created, owned_by="agent-lb"))
        items.append(entry.model_copy(update={"id": alias, "target_model": target}))
    await release_reservation(reservation)
    return ModelListResponse(
        data=items,
        models=[
            entry
            for entry in codex_model_entries(models, allowed_models, visibility_allowed_models)
            if model_is_visible(entry.slug)
        ],
    )
