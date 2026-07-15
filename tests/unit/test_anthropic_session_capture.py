from __future__ import annotations

import pytest

from app.core.anthropic.models import AnthropicMessageRequest
from app.modules.proxy.anthropic_service import _anthropic_request_session_id

pytestmark = pytest.mark.unit


def _payload(user_id: str | None = None) -> AnthropicMessageRequest:
    data: dict[str, object] = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "hello"}],
    }
    if user_id is not None:
        data["metadata"] = {"user_id": user_id}
    return AnthropicMessageRequest.model_validate(data)


def test_extracts_session_id_from_claude_code_json_user_id() -> None:
    user_id = (
        '{"device_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",'
        '"account_uuid":"","session_id":"a38d23ac-2d2f-4354-8861-5b686809b2b5"}'
    )
    assert _anthropic_request_session_id(_payload(user_id), {}) == "a38d23ac-2d2f-4354-8861-5b686809b2b5"


def test_extracts_legacy_session_suffix() -> None:
    user_id = "user_abc_session_a38d23ac-2d2f-4354-8861-5b686809b2b5"
    assert _anthropic_request_session_id(_payload(user_id), {}) == "a38d23ac-2d2f-4354-8861-5b686809b2b5"


def test_extracts_header_when_metadata_is_absent() -> None:
    headers = {"X-Claude-Code-Session-Id": " header-session "}
    assert _anthropic_request_session_id(_payload(), headers) == "header-session"


def test_metadata_session_id_wins_over_header() -> None:
    user_id = '{"session_id":"metadata-session"}'
    headers = {"X-Claude-Code-Session-Id": "header-session"}
    assert _anthropic_request_session_id(_payload(user_id), headers) == "metadata-session"


def test_malformed_json_falls_through_to_header() -> None:
    headers = {"X-Claude-Code-Session-Id": "header-session"}
    assert _anthropic_request_session_id(_payload("{not json"), headers) == "header-session"


def test_empty_json_session_id_falls_through_to_header() -> None:
    headers = {"X-Claude-Code-Session-Id": "header-session"}
    assert _anthropic_request_session_id(_payload('{"session_id":"   "}'), headers) == "header-session"


def test_absent_identity_returns_none() -> None:
    assert _anthropic_request_session_id(_payload(), {}) is None
