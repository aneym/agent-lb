from __future__ import annotations

from typing import Any

from app.core.providers.gemini_models import (
    GEMINI_DEFAULT_EFFORT,
    GEMINI_EFFORT_LEVELS,
    GEMINI_FLASH_LITE,
    GEMINI_HYDROGEN_SONAR,
    GeminiModel,
)

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


def gemini_session_id(body: dict[str, Any]) -> str | None:
    labels = body.get("labels")
    cascade_id = labels.get("cascade_id") if isinstance(labels, dict) else None
    for value in (cascade_id, body.get("sessionId")):
        if isinstance(value, str) and value.strip():
            return value.strip()[:128]
    return None


def _first_text(content: object) -> str:
    parts = content.get("parts") if isinstance(content, dict) else None
    if isinstance(parts, list) and parts and isinstance(parts[0], dict):
        text = parts[0].get("text")
        if isinstance(text, str):
            return text
    return ""


def antigravity_auxiliary_model(body: dict[str, Any]) -> GeminiModel | None:
    labels = body.get("labels")
    if not isinstance(labels, dict) or labels.get("client") != "antigravity-cli":
        return None
    if gemini_session_id(body) or body.get("tools") or body.get("cachedContent"):
        return None
    if _first_text(body.get("systemInstruction")).startswith("You are a conversation title generator."):
        return GEMINI_FLASH_LITE
    contents = body.get("contents")
    if body.get("systemInstruction") or not isinstance(contents, list) or len(contents) != 1:
        return None
    if not _first_text(contents[0]).startswith("You are a safety and security rater overseeing Jetski, an AI agent."):
        return None
    config = body.get("generationConfig", {})
    thinking = config.get("thinkingConfig")
    if not isinstance(thinking, dict):
        return None
    level = thinking.get("thinkingLevel")
    level = level.strip().lower() if isinstance(level, str) else None
    return {"minimal": GEMINI_FLASH_LITE, "low": GEMINI_HYDROGEN_SONAR}.get(level or "")


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
