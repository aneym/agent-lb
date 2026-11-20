from __future__ import annotations

from typing import Any


def _code_assist_generation_config(config: dict[str, Any]) -> dict[str, Any]:
    thinking = config.get("thinkingConfig")
    level = thinking.get("thinkingLevel") if isinstance(thinking, dict) else None
    if not isinstance(thinking, dict) or not isinstance(level, str):
        return config
    return {**config, "thinkingConfig": {**thinking, "thinkingLevel": level.strip().upper()}}


def code_assist_request(request: dict[str, Any], *, model: str, project: str, user_prompt_id: str) -> dict[str, Any]:
    wrapped = dict(request)
    session_id = wrapped.pop("sessionId", None)
    if session_id is not None:
        wrapped["session_id"] = session_id
    config = wrapped.get("generationConfig")
    if isinstance(config, dict):
        wrapped["generationConfig"] = _code_assist_generation_config(config)
    return {"model": model, "project": project, "user_prompt_id": user_prompt_id, "request": wrapped}


def gemini_chunk(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return None
    chunk = dict(response)
    trace_id = payload.get("traceId")
    if isinstance(trace_id, str) and trace_id and "responseId" not in chunk:
        chunk["responseId"] = trace_id
    return chunk
