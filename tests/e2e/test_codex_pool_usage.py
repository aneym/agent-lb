from __future__ import annotations

import pytest

from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.usage.repository import UsageRepository

POOL_HEADERS = {"Authorization": "Bearer header.placeholder.sig", "chatgpt-account-id": "agent-lb-pool"}


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_codex_home_without_login_reads_pool_usage(
    client,
    setup_dashboard_password,
    enable_api_key_auth,
    create_api_key,
    import_test_account,
):
    await import_test_account(client, account_id="acc-pool-usage", email="pool-usage@example.com")
    accounts = (await client.get("/api/accounts")).json()["accounts"]
    account_id = accounts[0]["accountId"]
    reset_at = int(utcnow().timestamp()) + 3600
    async with get_background_session() as session:
        usage = UsageRepository(session)
        await usage.add_entry(account_id, 30.0, window="primary", reset_at=reset_at, window_minutes=300)
        await usage.add_entry(account_id, 60.0, window="secondary", reset_at=reset_at, window_minutes=10080)

    pooled = await client.get("/api/codex/usage", headers=POOL_HEADERS)
    assert pooled.status_code == 200
    rate_limit = pooled.json()["rate_limit"]
    assert rate_limit["primary_window"]["used_percent"] == 30
    assert rate_limit["secondary_window"]["used_percent"] == 60

    await setup_dashboard_password(client)
    await enable_api_key_auth(client)
    refused = await client.get("/api/codex/usage", headers=POOL_HEADERS)
    assert refused.status_code == 401

    created = await create_api_key(client, name="pool-identity-key")
    keyed = await client.get("/api/codex/usage", headers={**POOL_HEADERS, "Authorization": f"Bearer {created['key']}"})
    assert keyed.status_code == 200
    assert keyed.json()["plan_type"] == "api_key"
