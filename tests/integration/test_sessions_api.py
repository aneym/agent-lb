from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.auth.dependencies import validate_dashboard_session
from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


async def _add_log(
    repository: RequestLogsRepository,
    *,
    request_id: str,
    session_id: str | None,
    model: str,
    requested_at,
    provider: str = "anthropic",
    useragent_group: str | None = "claude-cli",
    status: str = "success",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cached_input_tokens: int = 2,
    latency_ms: int = 10,
    reasoning_effort: str | None = None,
) -> None:
    await repository.add_log(
        account_id=None,
        request_id=request_id,
        session_id=session_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        latency_ms=latency_ms,
        reasoning_effort=reasoning_effort,
        status=status,
        error_code="upstream_error" if status != "success" else None,
        requested_at=requested_at,
        provider=provider,
        useragent_group=useragent_group,
    )


@pytest.mark.asyncio
async def test_sessions_list_groups_filters_and_sums(async_client, db_setup) -> None:
    del db_setup
    now = utcnow()
    async with SessionLocal() as session:
        repository = RequestLogsRepository(session)
        await _add_log(
            repository,
            request_id="req-session-a-1",
            session_id="session-a1234567",
            model="claude-fable-5",
            requested_at=now - timedelta(minutes=2),
            input_tokens=100,
            output_tokens=20,
            cached_input_tokens=10,
        )
        await _add_log(
            repository,
            request_id="req-session-a-2",
            session_id="session-a1234567",
            model="claude-opus-4-8",
            requested_at=now - timedelta(minutes=1),
            status="error",
            input_tokens=50,
            output_tokens=5,
            cached_input_tokens=4,
        )
        await _add_log(
            repository,
            request_id="req-old",
            session_id="session-old12345",
            model="claude-fable-5",
            requested_at=now - timedelta(minutes=61),
        )
        await _add_log(
            repository,
            request_id="req-http-turn",
            session_id="http_turn_0123456789abcdef0123456789abcdef",
            model="gpt-5.6-sol-medium",
            requested_at=now,
        )
        await _add_log(
            repository,
            request_id="req-turn",
            session_id="turn_0123456789abcdef0123456789abcdef",
            model="gpt-5.6-sol-medium",
            requested_at=now,
        )
        await _add_log(
            repository,
            request_id="req-null",
            session_id=None,
            model="claude-fable-5",
            requested_at=now,
        )

    response = await async_client.get("/api/sessions?windowMinutes=60&limit=50&offset=0")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    aggregate = body["sessions"][0]
    assert aggregate["sessionId"] == "session-a1234567"
    assert aggregate["provider"] == "anthropic"
    assert aggregate["useragentGroup"] == "claude-cli"
    assert aggregate["models"] == [
        {"model": "claude-fable-5", "requests": 1},
        {"model": "claude-opus-4-8", "requests": 1},
    ]
    assert aggregate["requests"] == 2
    assert aggregate["inputTokens"] == 150
    assert aggregate["outputTokens"] == 25
    assert aggregate["cachedInputTokens"] == 14
    assert aggregate["costUsd"] > 0
    assert aggregate["errors"] == 1


@pytest.mark.asyncio
async def test_session_detail_returns_breakdown_and_recent_requests(async_client, db_setup) -> None:
    del db_setup
    now = utcnow()
    async with SessionLocal() as session:
        repository = RequestLogsRepository(session)
        await _add_log(
            repository,
            request_id="req-detail-1",
            session_id="session-detail123",
            model="claude-fable-5",
            requested_at=now - timedelta(minutes=1),
        )
        await _add_log(
            repository,
            request_id="req-detail-2",
            session_id="session-detail123",
            model="claude-fable-5",
            requested_at=now,
        )

    response = await async_client.get("/api/sessions/session-detail123")

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["requests"] == 2
    assert body["byModel"] == [
        {
            "model": "claude-fable-5",
            "requests": 2,
            "inputTokens": 20,
            "outputTokens": 10,
            "cachedInputTokens": 4,
            "costUsd": pytest.approx(body["session"]["costUsd"]),
        }
    ]
    assert [row["requestId"] for row in body["recentRequests"]] == ["req-detail-2", "req-detail-1"]
    assert all(row["sessionId"] == "session-detail123" for row in body["recentRequests"])

    missing = await async_client.get("/api/sessions/missing-session")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_session_analytics_groups_series_seats_and_histograms(async_client, db_setup) -> None:
    del db_setup
    now = utcnow()
    session_id = "session-analytics123"
    async with SessionLocal() as session:
        repository = RequestLogsRepository(session)
        await _add_log(
            repository,
            request_id="req-analytics-outside-window",
            session_id=session_id,
            model="claude-opus-4-8",
            requested_at=now - timedelta(minutes=61),
            status="error",
            input_tokens=9_000,
            output_tokens=8_000,
            cached_input_tokens=7_000,
            latency_ms=30_000,
        )
        await _add_log(
            repository,
            request_id="req-analytics-medium",
            session_id=session_id,
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            requested_at=now - timedelta(minutes=4),
            input_tokens=80,
            output_tokens=20,
            cached_input_tokens=7,
            latency_ms=1_000,
        )
        await _add_log(
            repository,
            request_id="req-analytics-xhigh",
            session_id=session_id,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            requested_at=now - timedelta(minutes=2),
            status="error",
            input_tokens=400,
            output_tokens=100,
            cached_input_tokens=20,
            latency_ms=2_000,
        )
        await _add_log(
            repository,
            request_id="req-analytics-driver",
            session_id=session_id,
            model="claude-fable-5",
            requested_at=now - timedelta(minutes=1),
            input_tokens=49_000,
            output_tokens=1_000,
            cached_input_tokens=100,
            latency_ms=60_000,
        )

    response = await async_client.get(
        f"/api/sessions/{session_id}/analytics",
        params={"windowMinutes": 60},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bucketSeconds"] == 120
    assert body["session"]["requests"] == 3
    assert body["session"]["inputTokens"] == 49_480
    assert body["session"]["outputTokens"] == 1_120
    assert body["session"]["cachedInputTokens"] == 127
    assert body["session"]["errors"] == 1
    assert {model["model"] for model in body["session"]["models"]} == {
        "claude-fable-5",
        "gpt-5.6-sol",
    }
    assert sum(len(bucket["byModel"]) for bucket in body["series"]) == 3
    assert sum(
        model["outputTokens"]
        for bucket in body["series"]
        for model in bucket["byModel"]
    ) == body["session"]["outputTokens"]
    assert [
        (seat["model"], seat["reasoningEffort"], seat["requests"], seat["errors"])
        for seat in body["seats"]
    ] == [
        ("claude-fable-5", None, 1, 0),
        ("gpt-5.6-sol", "medium", 1, 0),
        ("gpt-5.6-sol", "xhigh", 1, 1),
    ]
    assert body["latencyHistogram"] == [
        {"label": "0-1s", "count": 0},
        {"label": "1-2s", "count": 2},
        {"label": "2-5s", "count": 0},
        {"label": "5-10s", "count": 0},
        {"label": "10-30s", "count": 0},
        {"label": "30-60s", "count": 1},
        {"label": ">60s", "count": 0},
    ]
    assert body["tokensPerRequestHistogram"] == [
        {"label": "<100", "count": 0},
        {"label": "100-500", "count": 2},
        {"label": "500-2K", "count": 0},
        {"label": "2K-10K", "count": 0},
        {"label": "10K-50K", "count": 1},
        {"label": ">50K", "count": 0},
    ]

    missing = await async_client.get("/api/sessions/missing-session/analytics")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_sessions_list_includes_zero_filled_sparkline(async_client, db_setup) -> None:
    del db_setup
    now = utcnow()
    async with SessionLocal() as session:
        repository = RequestLogsRepository(session)
        for index, minutes in enumerate((55, 5)):
            await _add_log(
                repository,
                request_id=f"req-sparkline-{index}",
                session_id="session-sparkline123",
                model="claude-fable-5",
                requested_at=now - timedelta(minutes=minutes),
            )

    response = await async_client.get("/api/sessions", params={"windowMinutes": 60})

    assert response.status_code == 200
    sparkline = response.json()["sessions"][0]["sparkline"]
    assert len(sparkline) == 24
    assert sum(sparkline) == 2
    assert len([count for count in sparkline if count]) == 2


@pytest.mark.asyncio
async def test_sessions_router_declares_dashboard_auth_dependency(app_instance) -> None:
    sessions_routes = [
        route
        for route in app_instance.routes
        if getattr(route, "path", "").startswith("/api/sessions")
    ]
    assert sessions_routes
    for route in sessions_routes:
        dependencies = [dependency.call for dependency in route.dependant.dependencies]
        assert validate_dashboard_session in dependencies


@pytest.mark.asyncio
async def test_session_short_link_redirects_full_or_unique_prefix(async_client, db_setup) -> None:
    del db_setup
    now = utcnow()
    async with SessionLocal() as session:
        repository = RequestLogsRepository(session)
        await _add_log(
            repository,
            request_id="req-short",
            session_id="a38d23ac-2d2f-4354-8861-5b686809b2b5",
            model="claude-fable-5",
            requested_at=now,
        )

    prefix_response = await async_client.get("/s/a38d23ac", follow_redirects=False)
    assert prefix_response.status_code == 302
    assert prefix_response.headers["location"] == "/sessions?session=a38d23ac-2d2f-4354-8861-5b686809b2b5"

    full_response = await async_client.get(
        "/s/a38d23ac-2d2f-4354-8861-5b686809b2b5",
        follow_redirects=False,
    )
    assert full_response.status_code == 302


@pytest.mark.asyncio
async def test_session_short_link_rejects_unknown_short_and_ambiguous_prefix(async_client, db_setup) -> None:
    del db_setup
    now = utcnow()
    async with SessionLocal() as session:
        repository = RequestLogsRepository(session)
        for suffix in ("1111", "2222"):
            await _add_log(
                repository,
                request_id=f"req-ambiguous-{suffix}",
                session_id=f"a38d23ac-{suffix}-4354-8861-5b686809b2b5",
                model="claude-fable-5",
                requested_at=now,
            )

    assert (await async_client.get("/s/unknown1", follow_redirects=False)).status_code == 404
    assert (await async_client.get("/s/a38d23a", follow_redirects=False)).status_code == 404
    ambiguous = await async_client.get("/s/a38d23ac", follow_redirects=False)
    assert ambiguous.status_code == 409
    assert "location" not in ambiguous.headers
