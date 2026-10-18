from __future__ import annotations

import base64
import json

from app.core.auth import generate_unique_account_id


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _server_error_sse_event() -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "server_error",
                    "message": "An error occurred while processing your request.",
                },
            },
        }
    )


def _stream_timeout_sse_event() -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "upstream_request_timeout",
                    "message": "Proxy request budget exhausted",
                },
            },
        }
    )


def _stream_incomplete_sse_event(response_id: str = "resp_incomplete") -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "id": response_id,
                "status": "failed",
                "error": {
                    "code": "stream_incomplete",
                    "message": "Upstream websocket closed before response.completed",
                },
            },
        }
    )


def _success_sse_event(response_id: str = "resp_ok") -> str:
    return _sse_event(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
    )


def _extract_events(lines: list[str]) -> list[dict]:
    events = []
    for line in lines:
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                continue
            events.append(json.loads(data))
    return events
