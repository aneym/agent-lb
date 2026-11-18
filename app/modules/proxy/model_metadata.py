from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.core.config.settings import get_settings
from app.core.openai.model_registry import UpstreamModel, is_public_model
from app.core.types import JsonValue
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.constants import V1_MAX_OUTPUT_TOKEN_OVERRIDES
from app.modules.proxy.request_policy import resolve_model_alias
from app.modules.proxy.schemas import CodexModelEntry, ModelMetadata, ReasoningLevelSchema


def codex_model_entries(
    models: Mapping[str, UpstreamModel],
    allowed_models: set[str] | None,
    visibility_allowed_models: set[str] | None,
) -> list[CodexModelEntry]:
    entries: list[CodexModelEntry] = []
    for slug, model in models.items():
        if visibility_allowed_models is None:
            if not is_public_model(model, allowed_models):
                continue
            entries.append(to_codex_model_entry(model))
            continue
        entries.append(
            to_codex_model_entry(
                model,
                visibility="list" if slug in visibility_allowed_models else "hide",
            )
        )
    return entries


def allowed_models_for_api_key(api_key: ApiKeyData | None) -> set[str] | None:
    allowed_models = canonical_model_set(api_key.allowed_models) if api_key and api_key.allowed_models else None
    if api_key and api_key.enforced_model:
        forced = {canonical_model_slug(api_key.enforced_model)}
        return forced if allowed_models is None else (allowed_models & forced)
    return allowed_models


def canonical_model_set(models: Iterable[str]) -> set[str]:
    return {canonical_model_slug(model) for model in models}


def canonical_model_slug(model: str) -> str:
    return resolve_model_alias(model) or model


def codex_model_visibility_allowed_models(api_key: ApiKeyData | None) -> set[str] | None:
    if api_key is None or not api_key.apply_to_codex_model or not api_key.allowed_models:
        return None
    return allowed_models_for_api_key(api_key)


def to_codex_model_entry(model: UpstreamModel, *, visibility: str | None = None) -> CodexModelEntry:
    raw = model.raw

    extra: dict[str, JsonValue] = {}
    skip_keys = {
        "slug",
        "display_name",
        "description",
        "base_instructions",
        "default_reasoning_level",
        "supported_reasoning_levels",
        "supported_in_api",
        "priority",
        "minimal_client_version",
        "supports_reasoning_summaries",
        "support_verbosity",
        "default_verbosity",
        "supports_parallel_tool_calls",
        "context_window",
        "input_modalities",
        "available_in_plans",
        "prefer_websockets",
        "visibility",
    }
    for key, value in raw.items():
        if key not in skip_keys and isinstance(value, (bool, int, float, str, type(None), list, Mapping)):
            extra[key] = value

    effective_cw = effective_context_window(model)
    if effective_cw != model.context_window and "max_context_window" in extra:
        extra["max_context_window"] = effective_cw

    return CodexModelEntry(
        slug=model.slug,
        display_name=model.display_name,
        description=model.description,
        base_instructions=model.base_instructions,
        default_reasoning_level=model.default_reasoning_level,
        supported_reasoning_levels=[
            ReasoningLevelSchema(effort=rl.effort, description=rl.description)
            for rl in model.supported_reasoning_levels
        ],
        supported_in_api=model.supported_in_api,
        priority=model.priority,
        minimal_client_version=model.minimal_client_version,
        supports_reasoning_summaries=model.supports_reasoning_summaries,
        support_verbosity=model.support_verbosity,
        default_verbosity=model.default_verbosity,
        supports_parallel_tool_calls=model.supports_parallel_tool_calls,
        context_window=effective_context_window(model),
        input_modalities=list(model.input_modalities),
        available_in_plans=sorted(model.available_in_plans),
        prefer_websockets=model.prefer_websockets,
        visibility=visibility or model_visibility(model),
        **extra,
    )


def effective_context_window(model: UpstreamModel) -> int:
    overrides = get_settings().model_context_window_overrides
    return overrides.get(model.slug, model.context_window)


def v1_full_context_window(model: UpstreamModel) -> int:
    overrides = get_settings().model_context_window_overrides
    return overrides.get(model.slug, model.context_window)


def v1_input_context_window(model: UpstreamModel) -> int:
    return model.context_window


def v1_max_output_tokens(model: UpstreamModel) -> int | None:
    return V1_MAX_OUTPUT_TOKEN_OVERRIDES.get(model.slug)


def v1_model_capabilities(model: UpstreamModel) -> dict[str, JsonValue]:
    return {
        "context_length": v1_input_context_window(model),
        "max_output_tokens": v1_max_output_tokens(model),
        "supports_reasoning": v1_supports_reasoning(model),
        "supports_images": v1_supports_vision(model),
        "supportsImages": v1_supports_vision(model),
        "supports_vision": v1_supports_vision(model),
        "supports_tool_use": True,
        "supports_streaming": True,
        "input_modalities": list(model.input_modalities),
        "output_modalities": ["text"],
    }


def v1_supports_reasoning(model: UpstreamModel) -> bool:
    return bool(model.supported_reasoning_levels) or model.supports_reasoning_summaries


def v1_supports_vision(model: UpstreamModel) -> bool:
    return "image" in model.input_modalities


def model_visibility(model: UpstreamModel) -> str:
    visibility = model.raw.get("visibility")
    return visibility if isinstance(visibility, str) else "list"


def to_model_metadata(model: UpstreamModel) -> ModelMetadata:
    return ModelMetadata(
        display_name=model.display_name,
        description=model.description,
        context_window=v1_full_context_window(model),
        input_context_window=v1_input_context_window(model),
        max_output_tokens=v1_max_output_tokens(model),
        input_modalities=list(model.input_modalities),
        supported_reasoning_levels=[
            ReasoningLevelSchema(effort=rl.effort, description=rl.description)
            for rl in model.supported_reasoning_levels
        ],
        default_reasoning_level=model.default_reasoning_level,
        supports_reasoning_summaries=model.supports_reasoning_summaries,
        support_verbosity=model.support_verbosity,
        default_verbosity=model.default_verbosity,
        prefer_websockets=model.prefer_websockets,
        supports_parallel_tool_calls=model.supports_parallel_tool_calls,
        supported_in_api=model.supported_in_api,
        minimal_client_version=model.minimal_client_version,
        priority=model.priority,
    )
