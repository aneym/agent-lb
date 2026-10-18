from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import generate_unique_account_id


@pytest.fixture(autouse=True)
async def _force_usage_weighted_routing(async_client) -> None:
    current = await async_client.get("/api/settings")
    assert current.status_code == 200
    payload = current.json()
    payload["routingStrategy"] = "usage_weighted"
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 200


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


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _extract_first_event(lines: list[str]) -> dict:

    for line in lines:
        if not line.startswith("data: ") or line.startswith("data: [DONE]"):
            continue
        event = json.loads(line[6:])
        if event.get("type") == "codex.keepalive":
            continue
        if event.get("type") == "response.created":
            response = event.get("response")
            if isinstance(response, dict) and response.get("status") == "in_progress" and response.get("output") == []:
                continue
        return event
    raise AssertionError("No SSE data event found")


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)
