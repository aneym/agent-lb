from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.team.service import reset_team_usage_cache

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_untrusted_member_lifecycle_through_proxy_routes(async_client, app_instance, monkeypatch):
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", "disabled")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS", "127.0.0.1/32,192.0.2.5/32")
    get_settings.cache_clear()
    settings = await async_client.put("/api/settings", json={"teamModeEnabled": True})
    assert settings.status_code == 200, settings.text
    created = await async_client.post("/api/team/members", json={"name": "Member", "costCapDayUsd": 5})
    assert created.status_code == 200, created.text
    member_id = created.json()["id"]
    keys = []
    for name in ("laptop", "desktop"):
        issued = await async_client.post(f"/api/team/members/{member_id}/keys", json={"name": name})
        assert issued.status_code == 200, issued.text
        keys.append(issued.json())

    transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
    async with AsyncClient(transport=transport, base_url="http://lb.example") as member:
        member.headers["X-Forwarded-For"] = "192.0.2.9"
        assert (await member.get("/v1/models")).status_code == 401
        member.headers["Authorization"] = "Bearer sk-clb-invalid"
        assert (await member.get("/v1/models")).status_code == 401
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
            assert denied.headers["X-Team-Window"] == "day"
            assert denied.headers["X-Team-Reset"].endswith("Z")

        listed = await async_client.get("/api/team/members")
        assert listed.json()[0]["usage"]["day"] == {"costUsd": 5.0, "tokens": 300}
        assert listed.json()[0]["gate"] == "over_cap"
        assert (await async_client.delete(f"/api/team/members/{member_id}")).status_code == 204
        assert (await member.get("/v1/models")).status_code == 200

        member.headers["X-Forwarded-For"] = "192.0.2.5"
        member.headers["Authorization"] = "Bearer junk"
        assert (await member.get("/v1/models")).status_code == 200
        assert (await member.get("/api/team/members")).status_code == 200
