from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from aiohttp import web
from sqlalchemy import update

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

OWNER = "frame-owner"
OTHER = "frame-other"


@asynccontextmanager
async def _artifact_upstream(seen: list[str]) -> AsyncIterator[str]:
    async def handler(request: web.Request) -> web.Response:
        bearer = request.headers.get("Authorization", "")
        seen.append(bearer)
        if bearer == f"Bearer access-{OWNER}":
            return web.json_response({"docs": [], "nextCursor": None})
        return web.Response(status=404, text="not found\n")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


async def _insert_anthropic_accounts() -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        for account_id in (OWNER, OTHER):
            session.add(
                Account(
                    id=account_id,
                    provider="anthropic",
                    chatgpt_account_id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="max",
                    access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
                    refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                    id_token_encrypted=None,
                    last_refresh=utcnow() + timedelta(days=1),
                    status=AccountStatus.ACTIVE,
                    deactivation_reason=None,
                )
            )
        await session.commit()


def _frame_post(async_client, seat: str):
    return async_client.post(
        "/api/frame/db/agent",
        json={"slug": "fixture", "verb": "list", "args": {"collection": "items", "limit": 100}},
        headers={"x-agent-lb-seat": seat, "X-Frame-CP": "go"},
    )


@pytest.mark.asyncio
async def test_frame_requests_use_only_the_configured_owner_account(async_client, monkeypatch):
    seen: list[str] = []
    await _insert_anthropic_accounts()
    async with _artifact_upstream(seen) as base_url:
        monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base_url)
        monkeypatch.setenv("AGENT_LB_ANTHROPIC_FRAME_ACCOUNT_ID", OWNER)
        get_settings.cache_clear()
        try:
            for index in range(8):
                response = await _frame_post(async_client, f"seat-{index}")
                assert response.status_code == 200
                assert response.json() == {"docs": [], "nextCursor": None}
            assert seen == [f"Bearer access-{OWNER}"] * 8

            async with SessionLocal() as session:
                await session.execute(update(Account).where(Account.id == OWNER).values(status=AccountStatus.PAUSED))
                await session.commit()
            seen.clear()
            response = await _frame_post(async_client, "seat-paused")
            assert response.status_code == 503
            assert "AGENT_LB_ANTHROPIC_FRAME_ACCOUNT_ID" in response.text
            assert OWNER in response.text
            assert seen == []
        finally:
            get_settings.cache_clear()
