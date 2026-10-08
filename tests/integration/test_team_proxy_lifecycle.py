from __future__ import annotations

import asyncio
import base64
import gzip
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import aiohttp
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.core.clients.proxy as proxy_client_module
import app.core.clients.proxy_websocket as websocket_client_module
import app.modules.proxy.service as proxy_module
from app.core import conversation_archive
from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.team.service import reset_team_usage_cache

pytestmark = pytest.mark.integration


def _account_auth_json() -> dict[str, object]:
    claims = {"chatgpt_account_id": "account-member", "email": "member-account@example.com"}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {
        "tokens": {
            "idToken": f"header.{encoded}.sig",
            "accessToken": "lb-account-token",
            "refreshToken": "refresh-token",
            "accountId": "account-member",
        }
    }


@pytest.mark.asyncio
async def test_untrusted_member_lifecycle_through_proxy_routes(async_client, app_instance, monkeypatch):
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", "disabled")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS", "127.0.0.1/32,100.64.1.5/32")
    get_settings.cache_clear()
    settings = await async_client.put("/api/settings", json={"teamModeEnabled": True})
    assert settings.status_code == 200, settings.text
    created = await async_client.post("/api/team/members", json={"name": "Member", "costCapDayUsd": 5})
    assert created.status_code == 200, created.text
    member_id = created.json()["id"]
    auth_json = _account_auth_json()
    imported = await async_client.post(
        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    )
    assert imported.status_code == 200, imported.text
    account_id = imported.json()["accountId"]
    keys = []
    for name in ("laptop", "desktop"):
        issued = await async_client.post(
            f"/api/team/members/{member_id}/keys", json={"name": name, "assignedAccountIds": [account_id]}
        )
        assert issued.status_code == 200, issued.text
        keys.append(issued.json())

    transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
    async with AsyncClient(transport=transport, base_url="http://lb.example") as member:
        member.headers["X-Forwarded-For"] = "100.64.9.9"
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = "Bearer chatgpt-oauth"
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["x-api-key"] = keys[0]["key"]
        assert (await member.get("/v1/models")).status_code == 200
        del member.headers["x-api-key"]
        member.headers["Authorization"] = "Bearer sk-clb-invalid"
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = "Bearer chatgpt-oauth"
        member.headers["x-api-key"] = "sk-clb-invalid"
        assert (await member.get("/v1/models")).status_code == 401
        del member.headers["x-api-key"]
        member.headers["Authorization"] = f"Bearer {keys[0]['key']}"
        assert (await member.get("/v1/models")).status_code == 200
        for path in ("/api/team/members", "/api/settings", "/api/accounts"):
            denied = await member.get(path)
            assert denied.status_code == 403, denied.text
            assert denied.json()["error"]["code"] == "team_mode_untrusted_client"
        assert (await member.get("/v1/usage")).status_code == 200

        updated = await async_client.patch(f"/api/team/members/{member_id}", json={"status": "suspended"})
        assert updated.status_code == 200
        for key in keys:
            member.headers["Authorization"] = f"Bearer {key['key']}"
            denied = await member.get("/v1/models")
            assert denied.status_code == 403, denied.text
            assert denied.json()["error"]["type"] == "team_member_suspended"
            assert denied.json()["error"]["message"].endswith("Text Alex if you have any questions.")
        assert (await member.get("/v1/usage")).status_code == 200

        updated = await async_client.patch(
            f"/api/team/members/{member_id}", json={"status": "active", "allowedModels": ["model-alpha"]}
        )
        assert updated.status_code == 200
        assert (await member.get("/v1/models")).status_code == 200
        for path, payload in (
            ("/v1/responses", {"model": "model-beta", "input": "hello"}),
            (
                "/v1/messages",
                {"model": "model-beta", "max_tokens": 8, "messages": [{"role": "user", "content": "hello"}]},
            ),
        ):
            denied = await member.post(path, json=payload)
            assert denied.status_code == 403, denied.text
            assert denied.json()["error"]["type"] == "team_model_not_allowed"

        async with SessionLocal() as session:
            for index, key in enumerate(keys):
                session.add(
                    RequestLog(
                        api_key_id=key["id"],
                        request_id=f"member-usage-{index}",
                        model="model-alpha",
                        status="success",
                        cost_usd=2.5,
                        input_tokens=100,
                        output_tokens=50,
                        requested_at=utcnow(),
                    )
                )
            await session.commit()
        reset_team_usage_cache()
        for key in keys:
            member.headers["Authorization"] = f"Bearer {key['key']}"
            denied = await member.get("/v1/models")
            assert denied.status_code == 429, denied.text
            assert denied.json()["error"]["type"] == "team_member_over_cap"
            assert denied.json()["error"]["message"].endswith("Text Alex if you have any questions.")
            assert denied.headers["X-Team-Window"] == "day"
            assert denied.headers["X-Team-Reset"].endswith("Z")

        listed = await async_client.get("/api/team/members")
        assert listed.json()[0]["usage"]["day"] == {"costUsd": 5.0, "tokens": 300}
        assert listed.json()[0]["gate"] == "over_cap"
        assert (await async_client.delete(f"/api/team/members/{member_id}")).status_code == 204
        assert (await member.get("/v1/models")).status_code == 200

        member.headers["X-Forwarded-For"] = "100.64.1.5"
        member.headers["Authorization"] = "Bearer junk"
        assert (await member.get("/v1/models")).status_code == 200
        member.headers["x-api-key"] = "sk-clb-invalid"
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = "Bearer sk-clb-invalid"
        member.headers["x-api-key"] = keys[0]["key"]
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = "Bearer junk"
        revoked = await async_client.patch(f"/api/api-keys/{keys[0]['id']}", json={"isActive": False})
        assert revoked.status_code == 200, revoked.text
        member.headers["x-api-key"] = keys[0]["key"]
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = f"Bearer {keys[0]['key']}"
        member.headers["x-api-key"] = keys[1]["key"]
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = "Bearer junk"
        expired = await async_client.patch(
            f"/api/api-keys/{keys[1]['id']}", json={"expiresAt": datetime(2020, 1, 1, tzinfo=UTC).isoformat()}
        )
        assert expired.status_code == 200, expired.text
        member.headers["x-api-key"] = keys[1]["key"]
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = f"Bearer {keys[1]['key']}"
        member.headers["x-api-key"] = "sk-clb-invalid"
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = "Bearer junk"
        del member.headers["x-api-key"]
        assert (await member.get("/v1/models")).status_code == 200
        assert (await member.get("/api/team/members")).status_code == 200


@pytest.mark.asyncio
async def test_member_header_chatgpt_bearer_is_not_upstream_or_persisted(
    async_client, app_instance, monkeypatch, tmp_path
):
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", "disabled")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("AGENT_LB_UPSTREAM_STREAM_TRANSPORT", "http")
    get_settings.cache_clear()
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: SimpleNamespace(
            conversation_archive_enabled=True,
            conversation_archive_dir=tmp_path,
            conversation_archive_queue_max_bytes=8 * 1024 * 1024,
        ),
    )
    assert (await async_client.put("/api/settings", json={"teamModeEnabled": True})).status_code == 200
    created = await async_client.post("/api/team/members", json={"name": "Member"})
    assert created.status_code == 200, created.text
    auth_json = _account_auth_json()
    imported = await async_client.post(
        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    )
    assert imported.status_code == 200, imported.text
    issued = await async_client.post(
        f"/api/team/members/{created.json()['id']}/keys",
        json={"name": "Desktop", "assignedAccountIds": [imported.json()["accountId"]]},
    )
    assert issued.status_code == 200, issued.text
    key = issued.json()

    captured_headers: list[dict[str, str]] = []

    class UpstreamContent:
        async def iter_chunked(self, _size):
            yield b'data: {"type":"response.completed","response":{"id":"member_resp_1"}}\n\n'

    class UpstreamResponse:
        status = 200
        content = UpstreamContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class UpstreamSession:
        def post(self, url, *, json, headers, timeout):
            captured_headers.append(dict(headers))
            return UpstreamResponse()

    async def fake_core_stream(payload, headers, access_token, account_id, **kwargs):
        async for event in proxy_client_module.stream_responses(
            payload,
            headers,
            access_token,
            account_id,
            session=cast(aiohttp.ClientSession, UpstreamSession()),
            allow_direct_egress=True,
            **{key: value for key, value in kwargs.items() if key != "allow_direct_egress"},
        ):
            yield event

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_core_stream)
    sentinel = "chatgpt-SENTINEL-abc123"
    headers = {
        "X-Forwarded-For": "100.64.9.9",
        "Authorization": f"Bearer {sentinel}",
        "x-api-key": key["key"],
    }
    transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
    async with AsyncClient(transport=transport, base_url="http://lb.example") as member:
        response = await member.post(
            "/backend-api/codex/responses",
            json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert "response.completed" in response.text

    assert len(captured_headers) == 1
    assert captured_headers[0]["Authorization"] == "Bearer lb-account-token"
    assert not any(name.lower() == "x-api-key" for name in captured_headers[0])
    assert sentinel not in json.dumps(captured_headers)

    async with SessionLocal() as session:
        logs = (await session.execute(select(RequestLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].api_key_id == key["id"]
        for log in logs:
            assert sentinel not in json.dumps(
                {column.name: getattr(log, column.name) for column in RequestLog.__table__.columns}, default=str
            )

    conversation_archive.flush_archive_writer()
    records = []
    for path in tmp_path.glob("*.jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as archive:
            records.extend(archive.read().splitlines())
    assert records
    assert not any(sentinel in record for record in records)


@pytest.mark.asyncio
async def test_member_header_websocket_bearer_is_not_upstream_or_persisted(
    async_client, app_instance, monkeypatch, tmp_path
):
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", "disabled")
    monkeypatch.setenv("AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS", "127.0.0.1/32")
    get_settings.cache_clear()
    monkeypatch.setattr(
        conversation_archive,
        "get_settings",
        lambda: SimpleNamespace(
            conversation_archive_enabled=True,
            conversation_archive_dir=tmp_path,
            conversation_archive_queue_max_bytes=8 * 1024 * 1024,
        ),
    )
    assert (await async_client.put("/api/settings", json={"teamModeEnabled": True})).status_code == 200
    created = await async_client.post("/api/team/members", json={"name": "Member"})
    assert created.status_code == 200, created.text
    auth_json = _account_auth_json()
    imported = await async_client.post(
        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    )
    assert imported.status_code == 200, imported.text
    issued = await async_client.post(
        f"/api/team/members/{created.json()['id']}/keys",
        json={"name": "Desktop", "assignedAccountIds": [imported.json()["accountId"]]},
    )
    assert issued.status_code == 200, issued.text
    key = issued.json()

    captured_headers: list[dict[str, str]] = []

    class UpstreamConnection:
        def __init__(self):
            self.messages = asyncio.Queue()

        async def send(self, _message):
            await self.messages.put('{"type":"response.created","response":{"id":"member_ws_resp_1"}}')
            await self.messages.put(
                '{"type":"response.completed","response":{"id":"member_ws_resp_1",'
                '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}'
            )

        async def recv(self):
            return await self.messages.get()

        async def close(self):
            return None

    async def fake_websocket_connect(_url, **kwargs):
        captured_headers.append(dict(kwargs["additional_headers"]))
        return UpstreamConnection()

    monkeypatch.setattr(websocket_client_module, "websocket_connect", fake_websocket_connect)
    sentinel = "chatgpt-WS-SENTINEL-abc123"
    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={"Authorization": f"Bearer {sentinel}", "x-api-key": key["key"]},
        ) as websocket:
            websocket.send_json({"type": "response.create", "model": "gpt-5.1", "input": "hi", "stream": True})
            assert websocket.receive_json()["type"] == "response.created"
            assert websocket.receive_json()["type"] == "response.completed"

    assert len(captured_headers) == 1
    assert captured_headers[0]["Authorization"] == "Bearer lb-account-token"
    assert not any(name.lower() == "x-api-key" for name in captured_headers[0])
    assert sentinel not in json.dumps(captured_headers)

    async with SessionLocal() as session:
        logs = (await session.execute(select(RequestLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].api_key_id == key["id"]
        assert logs[0].transport == "websocket"
        for log in logs:
            assert sentinel not in json.dumps(
                {column.name: getattr(log, column.name) for column in RequestLog.__table__.columns}, default=str
            )

    conversation_archive.flush_archive_writer()
    records = []
    for path in tmp_path.glob("*.jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as archive:
            records.extend(archive.read().splitlines())
    assert records
    assert not any(sentinel in record for record in records)
