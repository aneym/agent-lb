from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.pools.pace_curve import Account, pool_pace


def test_missing_quota_demotes_previously_rich_pool() -> None:
    now = datetime(2026, 10, 26, tzinfo=UTC)
    accounts = [Account(str(i), 100, now + timedelta(days=7), now) for i in range(3)]
    pace = pool_pace(
        accounts,
        now,
        fast=0,
        slow=0,
        previous={"tier": "rich"},
        tick="current",
        incomplete=True,
    )
    assert pace["tier"] == "behind"
    assert pace["candidate_tier"] == "behind"
    assert pace["stale"] is True


@pytest.mark.asyncio
async def test_pools_and_shadow_endpoint_share_empty_quota_result(async_client) -> None:
    response = await async_client.get("/api/pools")
    shadow = await async_client.get("/api/pools/pace")
    assert response.status_code == shadow.status_code == 200
    pools = {pool["id"]: pool for pool in response.json()["pools"]}
    for pool_id, provider in (("anthropic-general", "anthropic"), ("openai-codex", "openai")):
        left = pools[pool_id]["pace"]
        right = shadow.json()["pools"][provider]
        assert left["tier"] == right["tier"] == "behind"
        assert left["stale"] is right["stale"] is True
        assert left["b_star"] == right["b_star"] == 0
        assert pools[pool_id]["admissionPacePercent"] is None
