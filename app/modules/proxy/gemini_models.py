from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.model_aliases import model_is_visible
from app.core.providers.gemini_models import (
    GEMINI_FLASH_LITE_MODEL,
    GEMINI_HYDROGEN_SONAR_MODEL,
    GEMINI_MODEL_REGISTRY,
    GeminiModel,
)

GEMINI_INPUT_TOKEN_LIMIT = 1_048_576
GEMINI_OUTPUT_TOKEN_LIMIT = 65_536
GEMINI_METHODS = ("generateContent", "streamGenerateContent")
_AUXILIARY_DISPLAY_NAMES = {
    GEMINI_FLASH_LITE_MODEL: "Gemini 3.5 Flash Lite",
    GEMINI_HYDROGEN_SONAR_MODEL: "Gemini 4 Hydrogen Sonar",
}


def model_visible(model: GeminiModel) -> bool:
    return model_is_visible(model.model_id) and model_is_visible(model.canonical_model)


def model_resource(model: GeminiModel) -> dict[str, Any]:
    level = model.effort
    base_name = _AUXILIARY_DISPLAY_NAMES.get(model.canonical_model, "Gemini 4 Neon")
    display_name = f"{base_name} ({level})" if level is not None and not model.auxiliary else base_name
    return {
        "name": f"models/{model.model_id}",
        "baseModelId": model.canonical_model,
        "version": "001",
        "displayName": display_name,
        "description": f"{base_name} served through agent-lb",
        "inputTokenLimit": GEMINI_INPUT_TOKEN_LIMIT,
        "outputTokenLimit": GEMINI_OUTPUT_TOKEN_LIMIT,
        "supportedGenerationMethods": list(GEMINI_METHODS),
        "thinking": True,
    }


def models_page(page_size: int | None, start: int, allowed: Callable[[GeminiModel], bool]) -> dict[str, Any]:
    choices = [model for model in GEMINI_MODEL_REGISTRY.models if model_visible(model) and allowed(model)]
    size = page_size if page_size and page_size > 0 else 50
    end = min(start + size, len(choices))
    page: dict[str, Any] = {"models": [model_resource(choice) for choice in choices[start:end]]}
    if end < len(choices):
        page["nextPageToken"] = str(end)
    return page
