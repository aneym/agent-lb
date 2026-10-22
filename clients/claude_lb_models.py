from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from claude_lb_common import CCGPT_MODE, CCGPT_MODEL_NAME  # pyrefly: ignore[missing-import]


class CcgptModelViolation(ValueError):
    pass


def ccgpt_upstream_path(path: str, body: bytes | None = None) -> str:
    if not CCGPT_MODE:
        return path
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return path
    exact_paths = {
        "/v1/messages",
        "/v1/messages/",
        "/v1/messages/count_tokens",
        "/v1/messages/count_tokens/",
    }
    if parsed.path not in exact_paths:
        return path
    normalized = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    suffix = path[len(parsed.path) :]
    trailing_slash = "/" if parsed.path.endswith("/") else ""
    if normalized == "/v1/messages/count_tokens":
        return f"/v1/ccgpt/messages/count_tokens{trailing_slash}{suffix}"
    requested = ccgpt_request_model(body)
    if not requested or not CCGPT_MODEL_NAME.match(requested):
        label = requested or "missing model"
        raise CcgptModelViolation(f"CCGPT only permits GPT models; rejected Messages request for {label}")
    return f"/v1/ccgpt/messages{trailing_slash}{suffix}"


def ccgpt_request_model(body: bytes | None) -> str | None:
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    return model if isinstance(model, str) else None
