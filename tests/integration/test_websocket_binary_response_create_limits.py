"""A response.create sent as a binary websocket frame meets the API key's limits (fix round, 2026-10-07).

Before the fix, once a text response.create had opened the upstream session, a binary frame
was forwarded raw: no preparation, no reservation, and no unpriced-model refusal under a
cost_usd cap. The upstream socket is faked at the network edge only; the key, its limits and
the reservation run through the real websocket route and database.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

import app.core.clients.proxy_websocket as websocket_client_module

pytestmark = pytest.mark.integration

UNPRICED_MODEL = "gpt-unpriced-test-0"


def _account_auth_json() -> dict[str, object]:
    claims = {"chatgpt_account_id": "account-ws-cap", "email": "ws-cap-account@example.com"}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {
        "tokens": {
            "idToken": f"header.{encoded}.sig",
            "accessToken": "lb-account-token",
            "refreshToken": "refresh-token",
            "accountId": "account-ws-cap",
        }
    }


@pytest.mark.asyncio
async def test_binary_response_create_is_refused_for_unpriced_model_under_cost_cap(
    async_client, app_instance, monkeypatch
):
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200, settings.text
    imported = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(_account_auth_json()), "application/json")},
    )
    assert imported.status_code == 200, imported.text
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "ws-cost-capped",
            "limits": [{"limitType": "cost_usd", "limitWindow": "daily", "maxValue": 10_000_000}],
        },
    )
    assert created.status_code == 200, created.text
    key = created.json()["key"]

    upstream_frames: list[str | bytes] = []

    class UpstreamConnection:
        def __init__(self):
            self.messages: asyncio.Queue[str] = asyncio.Queue()

        async def send(self, message):
            upstream_frames.append(message)
            response_id = f"ws_cap_resp_{len(upstream_frames)}"
            await self.messages.put(json.dumps({"type": "response.created", "response": {"id": response_id}}))
            await self.messages.put(
                json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    }
                )
            )

        async def recv(self):
            return await self.messages.get()

        async def close(self):
            return None

    async def fake_websocket_connect(_url, **_kwargs):
        return UpstreamConnection()

    monkeypatch.setattr(websocket_client_module, "websocket_connect", fake_websocket_connect)
    priced = {"type": "response.create", "model": "gpt-5.1", "input": "hi", "stream": True}
    unpriced = {"type": "response.create", "model": UNPRICED_MODEL, "input": "hi", "stream": True}
    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses", headers={"Authorization": f"Bearer {key}"}
        ) as websocket:
            websocket.send_json(priced)
            assert websocket.receive_json()["type"] == "response.created"
            assert websocket.receive_json()["type"] == "response.completed"

            websocket.send_bytes(json.dumps(unpriced).encode())
            refusal = websocket.receive_json()

            # A priced binary response.create still runs: prepared and sent upstream like a text one.
            websocket.send_bytes(json.dumps(priced).encode())
            assert websocket.receive_json()["type"] == "response.created"
            assert websocket.receive_json()["type"] == "response.completed"

    assert len(upstream_frames) == 2, upstream_frames
    assert upstream_frames[1] == upstream_frames[0]
    assert refusal["type"] == "error", refusal
    assert refusal["error"]["code"] == "model_unpriced_under_cost_cap", refusal
    assert repr(UNPRICED_MODEL) in refusal["error"]["message"]
