"""API-boundary receipts contract: independent token and time arithmetic."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import ApiKey, RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_receipts_and_summary(async_client, db_setup):
    # UTC hour buckets: 00:00, empty 01:00, 02:00, then exclusive 03:00.
    def timestamp(hour, minute=0):
        return datetime(2026, 9, 25, hour, minute, tzinfo=timezone.utc).replace(tzinfo=None)

    async with SessionLocal() as session:
        session.add_all(
            [
                ApiKey(id="key-a", name="Alpha", key_hash="hash-a", key_prefix="sk-a"),
                ApiKey(id="key-b", name="Beta", key_hash="hash-b", key_prefix="sk-b"),
            ]
        )
        from app.core.crypto import TokenEncryptor
        from app.db.models import Account, AccountStatus

        encryptor = TokenEncryptor()
        for aid in ("acc-a", "acc-b"):
            session.add(
                Account(
                    id=aid,
                    email=f"{aid}@example.invalid",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=timestamp(0),
                    status=AccountStatus.ACTIVE,
                )
            )
        await session.flush()
        rows = [
            # id, time, provider, account, key, session, client-session, raw input, read, write, output, cost, status
            (1, timestamp(0), "openai", "acc-a", "key-a", "sess-a", None, 100, 40, 10, 20, 1.0, "success"),
            (2, timestamp(0, 30), "anthropic", "acc-b", "key-b", "sess-b", "client-a", 50, 20, 5, 10, 2.0, "error"),
            (3, timestamp(2), "openai", "acc-a", "key-a", "sess-a", None, 30, 10, 0, 5, 0.5, "success"),
            (4, timestamp(2, 15), "anthropic", "acc-b", "key-b", "sess-b", None, 5, 0, 0, 0, 0.25, "success"),
            (5, timestamp(3), "openai", "acc-a", "key-a", "sess-a", None, 20, 0, 0, 0, 9.0, "success"),
            (6, timestamp(0, 45), "openai", "acc-a", "key-a", "sess-a", None, 999, 0, 0, 0, 9.0, "success"),
            (7, timestamp(0), "openai", "acc-a", "key-a", "sess-a", None, 8, 0, 0, 0, 8.0, "success"),
            (8, timestamp(2, 40), "anthropic", "acc-b", "key-b", "sess-b", None, 7, 0, 0, 1, 0.25, "success"),
        ]
        for rid, ts, provider, aid, kid, sid, client_sid, inp, read, write, out, cost, status in rows:
            session.add(
                RequestLog(
                    id=rid,
                    requested_at=ts,
                    provider=provider,
                    account_id=aid,
                    api_key_id=kid,
                    session_id=sid,
                    client_session_id=client_sid,
                    request_id=f"req-{rid}",
                    model="model-a" if aid == "acc-a" else "model-b",
                    status=status,
                    error_code="rate_limit" if status == "error" else None,
                    input_tokens=inp,
                    cache_read_tokens=read,
                    cache_creation_tokens=write,
                    output_tokens=out,
                    cost_usd=cost,
                    latency_ms=rid * 100,
                    deleted_at=timestamp(2) if rid == 6 else None,
                )
            )
        await session.commit()

    bounds = {"since": "2026-09-25T00:00:00Z", "until": "2026-09-25T03:00:00Z"}
    response = await async_client.get("/api/receipts", params=bounds)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 6  # 1,2,3,4,7,8: deleted 6 and exclusive 5 are absent
    assert [r["id"] for r in body["receipts"]] == [8, 4, 3, 2, 7, 1]
    first = next(r for r in body["receipts"] if r["id"] == 1)
    assert (first["pool"], first["inputTokens"], first["cacheReadTokens"], first["cacheReadRatio"]) == (
        "openai-codex",
        60,
        40,
        0.3636,
    )
    anthropic = next(r for r in body["receipts"] if r["id"] == 2)
    assert (
        anthropic["apiKeyName"],
        anthropic["apiKeyPrefix"],
        anthropic["inputTokens"],
        anthropic["status"],
        anthropic["errorCode"],
    ) == ("Beta", "sk-b", 50, "error", "rate_limit")
    for params, expected_ids in [
        ({"session_id": "client-a"}, [2]),
        ({"session_id": "sess-a", "status": "ok", "provider": "openai", "pool": "openai-codex"}, [3, 7, 1]),
        ({"account_id": "acc-b", "api_key_id": "key-b", "model": "model-b"}, [8, 4, 2]),
    ]:
        reply = await async_client.get("/api/receipts", params={**bounds, **params})
        assert reply.status_code == 200
        assert [row["id"] for row in reply.json()["receipts"]] == expected_ids
    page = (await async_client.get("/api/receipts", params={**bounds, "limit": 2, "offset": 2})).json()
    assert (page["total"], page["limit"], page["offset"], [r["id"] for r in page["receipts"]]) == (6, 2, 2, [3, 2])

    summary = (await async_client.get("/api/receipts/summary", params={**bounds, "bucket": "hour"})).json()
    # The row at the inclusive start (id 7) contributes 8 tokens and $8.
    assert summary["totals"] == {
        "requests": 6,
        "errors": 1,
        "errorRate": 1 / 6,
        "inputTokens": 150,
        "outputTokens": 36,
        "cacheReadTokens": 70,
        "cacheWriteTokens": 15,
        "tokens": 271,
        "cacheReadRatio": 0.2979,
        "costUsd": 12.0,
        "p50LatencyMs": 400,
        "p95LatencyMs": 800,
        "topErrorCode": "rate_limit",
    }
    assert summary["series"] == [
        {"start": "2026-09-25T00:00:00Z", "requests": 3, "byProvider": {"openai": 2, "anthropic": 1}},
        {"start": "2026-09-25T01:00:00Z", "requests": 0, "byProvider": {}},
        {"start": "2026-09-25T02:00:00Z", "requests": 3, "byProvider": {"openai": 1, "anthropic": 2}},
    ]
    account_groups = {(g["key"], g["provider"]): g for g in summary["groups"]}
    assert account_groups[("acc-a", "openai")] == {
        "key": "acc-a",
        "provider": "openai",
        "requests": 3,
        "errors": 0,
        "errorRate": 0.0,
        "tokens": 173,
        "cacheReadRatio": 0.3378,
        "costUsd": 9.5,
    }
    assert account_groups[("acc-b", "anthropic")] == {
        "key": "acc-b",
        "provider": "anthropic",
        "requests": 3,
        "errors": 1,
        "errorRate": 1 / 3,
        "tokens": 98,
        "cacheReadRatio": 0.2299,
        "costUsd": 2.5,
    }
    filtered = (await async_client.get(
        "/api/receipts/summary", params={**bounds, "session_id": "client-a", "group_by": "key"}
    )).json()
    assert (filtered["totals"]["requests"], filtered["totals"]["errors"],
            filtered["groups"][0]["key"], filtered["groups"][0]["tokens"]) == (1, 1, "key-b", 85)
    # Summary is never limited by a list page; repeated filters are OR within a dimension.
    repeated = (await async_client.get("/api/receipts/summary", params=[
        ("since", bounds["since"]), ("until", bounds["until"]),
        ("account_id", "acc-a"), ("account_id", "acc-b"),
        ("api_key_id", "key-a"), ("api_key_id", "key-b"),
    ])).json()
    assert repeated["totals"]["requests"] == 6
    daily = (await async_client.get("/api/receipts/summary", params={**bounds, "group_by": "day"})).json()
    assert daily["groups"] == [
        {
            "key": "2026-09-25",
            "provider": None,
            "requests": 6,
            "errors": 1,
            "errorRate": 1 / 6,
            "tokens": 271,
            "cacheReadRatio": 0.2979,
            "costUsd": 12.0,
        }
    ]


@pytest.mark.asyncio
async def test_receipts_requires_dashboard_auth_for_remote_clients(app_instance):
    async with app_instance.router.lifespan_context(app_instance):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance, client=("203.0.113.11", 50001)), base_url="http://lb.example"
        ) as client:
            for path in ("/api/receipts", "/api/receipts/summary"):
                response = await client.get(path)
                assert response.status_code == 401
