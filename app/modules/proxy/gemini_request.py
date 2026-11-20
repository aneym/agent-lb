from __future__ import annotations

from typing import Any

from app.core.providers.gemini_models import GEMINI_DEFAULT_EFFORT, GEMINI_EFFORT_LEVELS, GeminiModel

REQUEST_FIELDS = (
    ("contents", "contents"),
    ("systemInstruction", "system_instruction"),
    ("cachedContent", "cached_content"),
    ("tools", "tools"),
    ("toolConfig", "tool_config"),
    ("labels", "labels"),
    ("safetySettings", "safety_settings"),
    ("generationConfig", "generation_config"),
)


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


def _camel_keys(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    snake = {_camel(key): item for key, item in value.items() if "_" in key}
    return {**snake, **{key: item for key, item in value.items() if "_" not in key}}


def _generation_config(body: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(body.get("generationConfig"), dict) and not isinstance(body.get("generation_config"), dict):
        return None
    config = {**_camel_keys(body.get("generation_config")), **_camel_keys(body.get("generationConfig"))}
    if "thinkingConfig" in config:
        config["thinkingConfig"] = _camel_keys(config["thinkingConfig"])
    return config


def normalized_request(body: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {}
    for camel, snake in REQUEST_FIELDS:
        if camel == "generationConfig":
            config = _generation_config(body)
            if config is not None:
                request[camel] = config
        elif camel in body:
            request[camel] = body[camel]
        elif snake in body:
            request[camel] = body[snake]
    session_id = body.get("sessionId", body.get("session_id"))
    if isinstance(session_id, str) and session_id:
        request["sessionId"] = session_id
    return request


def apply_thinking_level(body: dict[str, Any], model: GeminiModel) -> str:
    config = body.get("generationConfig")
    if not isinstance(config, dict):
        config = {}
        body["generationConfig"] = config
    thinking = config.get("thinkingConfig")
    if not isinstance(thinking, dict):
        thinking = {}
        config["thinkingConfig"] = thinking
    if model.effort is not None:
        thinking.pop("thinkingBudget", None)
        thinking["thinkingLevel"] = model.effort
        return model.effort
    sent = thinking.get("thinkingLevel")
    if isinstance(sent, str) and sent.strip().lower() in GEMINI_EFFORT_LEVELS:
        level = sent.strip().lower()
        thinking["thinkingLevel"] = level
        return level
    thinking.pop("thinkingBudget", None)
    thinking["thinkingLevel"] = GEMINI_DEFAULT_EFFORT
    return GEMINI_DEFAULT_EFFORT
