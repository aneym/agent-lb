from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(frozen=True, slots=True)
class AnthropicErrorDetails:
    message: str
    error_type: str | None = None

    details: Any | None = None


async def read_error_message(resp: aiohttp.ClientResponse) -> str:
    return (await read_error_details(resp)).message


async def read_error_details(resp: aiohttp.ClientResponse) -> AnthropicErrorDetails:
    raw = await resp.read()
    if not raw:
        return AnthropicErrorDetails(f"Anthropic upstream returned HTTP {resp.status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return AnthropicErrorDetails(raw.decode("utf-8", errors="replace"))
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            error_type = error.get("type")
            if isinstance(message, str):
                return AnthropicErrorDetails(
                    message,
                    error_type if isinstance(error_type, str) else None,
                    error.get("details"),
                )
        if isinstance(payload.get("message"), str):
            error_type = payload.get("type")
            return AnthropicErrorDetails(
                payload["message"],
                error_type if isinstance(error_type, str) else None,
                payload.get("details"),
            )
    return AnthropicErrorDetails(f"Anthropic upstream returned HTTP {resp.status}")


def error_type_for_status(status_code: int) -> str:
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 529:
        return "overloaded_error"
    if status_code == 504:
        return "timeout_error"
    if status_code >= 500:
        return "api_error"
    if status_code in {401, 403}:
        return "authentication_error"
    if status_code == 404:
        return "not_found_error"
    return "invalid_request_error"
