from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.model_aliases import model_is_visible

GEMINI_PROVIDER = "gemini"
GEMINI_CANONICAL_MODEL = "gemini-4-neon"
GEMINI_DEFAULT_THINKING_LEVEL = "high"
GEMINI_THINKING_LEVELS = ("low", "medium", "high", "xhigh")
GEMINI_INPUT_TOKEN_LIMIT = 1_048_576
GEMINI_OUTPUT_TOKEN_LIMIT = 65_536
GEMINI_METHODS = ("generateContent", "streamGenerateContent")


@dataclass(frozen=True, slots=True)
class GeminiModelChoice:
    requested: str
    canonical: str
    thinking_level: str | None


def catalog_ids() -> list[str]:
    return [GEMINI_CANONICAL_MODEL, *(f"{GEMINI_CANONICAL_MODEL}-{level}" for level in GEMINI_THINKING_LEVELS)]


def visible_catalog_ids() -> list[str]:
    return [model_id for model_id in catalog_ids() if model_is_visible(model_id)]


def resolve_choice(requested: str) -> GeminiModelChoice | None:
    name = requested.removeprefix("models/").strip()
    if name == GEMINI_CANONICAL_MODEL:
        return GeminiModelChoice(name, GEMINI_CANONICAL_MODEL, None)
    for level in GEMINI_THINKING_LEVELS:
        if name == f"{GEMINI_CANONICAL_MODEL}-{level}":
            return GeminiModelChoice(name, GEMINI_CANONICAL_MODEL, level)
    return None


def choice_is_visible(choice: GeminiModelChoice) -> bool:
    return model_is_visible(choice.requested) and model_is_visible(choice.canonical)


def model_resource(choice: GeminiModelChoice) -> dict[str, Any]:
    level = choice.thinking_level
    return {
        "name": f"models/{choice.requested}",
        "baseModelId": choice.canonical,
        "version": "001",
        "displayName": "Gemini 4 Neon" if level is None else f"Gemini 4 Neon ({level})",
        "description": "Gemini 4 Neon served through agent-lb",
        "inputTokenLimit": GEMINI_INPUT_TOKEN_LIMIT,
        "outputTokenLimit": GEMINI_OUTPUT_TOKEN_LIMIT,
        "supportedGenerationMethods": list(GEMINI_METHODS),
        "thinking": True,
    }


def models_page(page_size: int | None, page_token: str | None) -> dict[str, Any]:
    choices = [choice for model_id in visible_catalog_ids() if (choice := resolve_choice(model_id)) is not None]
    start = int(page_token) if page_token and page_token.isdigit() else 0
    size = page_size if page_size and page_size > 0 else 50
    end = min(start + size, len(choices))
    page: dict[str, Any] = {"models": [model_resource(choice) for choice in choices[start:end]]}
    if end < len(choices):
        page["nextPageToken"] = str(end)
    return page


def apply_thinking_level(body: dict[str, Any], choice: GeminiModelChoice) -> str:
    config = body.get("generationConfig")
    if not isinstance(config, dict):
        config = {}
        body["generationConfig"] = config
    thinking = config.get("thinkingConfig")
    if not isinstance(thinking, dict):
        thinking = {}
        config["thinkingConfig"] = thinking
    if choice.thinking_level is not None:
        thinking.pop("thinkingBudget", None)
        thinking["thinkingLevel"] = choice.thinking_level
        return choice.thinking_level
    sent = thinking.get("thinkingLevel")
    if isinstance(sent, str) and sent.strip().lower() in GEMINI_THINKING_LEVELS:
        level = sent.strip().lower()
        thinking["thinkingLevel"] = level
        return level
    thinking.pop("thinkingBudget", None)
    thinking["thinkingLevel"] = GEMINI_DEFAULT_THINKING_LEVEL
    return GEMINI_DEFAULT_THINKING_LEVEL
