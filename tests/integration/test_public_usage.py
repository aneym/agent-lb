from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


async def _insert_log(
    *,
    request_id: str,
    provider: str,
    model: str,
    when,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    status: str = "success",
    latency_ms: int | None = 200,
) -> None:
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=None,
                api_key_id=None,
                request_id=request_id,
                model=model,
                provider=provider,
                requested_at=when,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=0,
                reasoning_tokens=0,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                status=status,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_public_usage_aggregates_without_auth(async_client):
    now = utcnow()
    yesterday = now - timedelta(days=1)
    await _insert_log(
        request_id="r1", provider="openai", model="gpt-5.4-mini", when=now,
        input_tokens=100, output_tokens=50, cost_usd=0.02,
    )
    await _insert_log(
        request_id="r2", provider="anthropic", model="claude-haiku-4-5-20251001", when=now,
        input_tokens=30, output_tokens=5, cost_usd=0.01,
    )
    await _insert_log(
        request_id="r3", provider="openai", model="gpt-5.4-mini", when=yesterday,
        input_tokens=200, output_tokens=100, cost_usd=0.04, status="error", latency_ms=100,
    )

    # No auth headers — this surface is public.
    response = await async_client.get("/api/usage/public?days=30")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "max-age=300" in response.headers.get("cache-control", "")

    body = response.json()
    assert set(body) >= {"period", "generatedAt", "source", "totals", "daily", "byModel", "byProvider", "trends"}
    assert body["source"] == "live"
    assert body["period"]["days"] == 30

    totals = body["totals"]
    assert totals["requests"] == 3
    assert totals["costUsd"] == pytest.approx(0.07)
    assert totals["inputTokens"] == 330
    assert totals["outputTokens"] == 155
    assert totals["successRate"] == pytest.approx(2 / 3, abs=1e-3)
    assert totals["avgLatencyMs"] is not None

    assert body["byProvider"]["openai"]["requests"] == 2
    assert body["byProvider"]["openai"]["costUsd"] == pytest.approx(0.06)
    assert body["byProvider"]["anthropic"]["requests"] == 1
    assert body["byProvider"]["anthropic"]["costUsd"] == pytest.approx(0.01)

    # byModel: descending by cost, with humanized label.
    by_model = body["byModel"]
    assert [m["model"] for m in by_model] == ["gpt-5.4-mini", "claude-haiku-4-5-20251001"]
    claude = next(m for m in by_model if m["provider"] == "anthropic")
    assert claude["label"] == "Claude Haiku 4.5"

    # daily is ascending and gap-free.
    dates = [d["date"] for d in body["daily"]]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    days_with_data = {d["date"]: d for d in body["daily"] if d["requests"] > 0}
    assert days_with_data[now.strftime("%Y-%m-%d")]["topModel"] == "gpt-5.4-mini"
    # Contract conformance: website types declare topModel:string and avgLatencyMs:number (non-null).
    empty_days = [d for d in body["daily"] if d["requests"] == 0]
    assert empty_days and all(d["topModel"] == "" for d in empty_days)
    assert isinstance(totals["avgLatencyMs"], (int, float))

    # Anonymization contract: no per-account / identifying fields anywhere in the payload.
    raw = json.dumps(body)
    for forbidden in ("accountId", "account_id", "apiKey", "api_key", "email"):
        assert forbidden not in raw
