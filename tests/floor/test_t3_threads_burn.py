from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.request_models import RequestLog
from app.db.session import SessionLocal
from app.db.t3_models import T3NativeSession, T3Thread

HOST = "ax42"
ROOT = "mcp:root-1"
CHILD = "thread:delegated-task:child-1"
SUBAGENT = "thread:provider:claudeAgent:subagent-1"
CODEX = "mcp:codex-1"
CLAUDE_NATIVE = "11111111-1111-4111-8111-111111111111"
CHILD_NATIVE = "22222222-2222-4222-8222-222222222222"
CODEX_NATIVE = "01a17983-0000-7000-8000-000000000003"


def thread(thread_id: str, title: str, root: str, **extra) -> dict:
    return {
        "threadId": thread_id,
        "title": title,
        "projectTitle": "Rails",
        "rootThreadId": root,
        "createdAt": "2026-10-26T15:07:12.197Z",
        **extra,
    }


def sync_body(root_title: str = "Recruiter app") -> dict:
    return {
        "host": HOST,
        "threads": [
            thread(ROOT, root_title, ROOT),
            thread(CHILD, "Delegated check", ROOT, parentThreadId=ROOT, relationship="subagent"),
            thread(SUBAGENT, "Inventory", ROOT, parentThreadId=ROOT, relationship="subagent"),
            thread(CODEX, "Codex task", CODEX, archivedAt="2026-10-26T18:00:00.000Z"),
        ],
        "nativeSessions": [
            {"nativeId": CLAUDE_NATIVE, "threadId": ROOT, "driver": "claudeAgent", "providerInstance": "claudeAgent"},
            {"nativeId": CHILD_NATIVE, "threadId": CHILD, "driver": "claudeAgent"},
            {"nativeId": CODEX_NATIVE, "threadId": CODEX, "driver": "codex"},
        ],
    }


def account(account_id: str, provider: str, plan: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        provider=provider,
        email=f"{account_id}@example.test",
        plan_type=plan,
        access_token_encrypted=encryptor.encrypt("a"),
        refresh_token_encrypted=encryptor.encrypt("r"),
        id_token_encrypted=encryptor.encrypt("i"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


def weekly(account_id: str, provider: str, at, used: float, reset: int) -> UsageHistory:
    return UsageHistory(
        account_id=account_id,
        provider=provider,
        recorded_at=at,
        window="secondary",
        window_minutes=10080,
        used_percent=used,
        reset_at=reset,
    )


def request(n: int, account_id: str, provider: str, at, model: str, session: str | None, cost: float, **extra):
    return RequestLog(
        account_id=account_id,
        provider=provider,
        request_id=f"req-{n}",
        requested_at=at,
        model=model,
        status="success",
        client_session_id=session,
        cost_usd=cost,
        input_tokens=100,
        output_tokens=10,
        **extra,
    )


async def seed() -> None:
    now = utcnow()
    base = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    reset = int((now + timedelta(days=3)).timestamp())
    busy = base + timedelta(hours=2, minutes=20)
    async with SessionLocal() as session:
        session.add_all([account("claude-1", "anthropic", "claude"), account("codex-1", "openai", "pro")])
        await session.flush()
        series = [(10.0, 40.0), (10.0, 42.0), (10.0, 42.0), (12.0, 46.0)]
        for offset, (claude_used, codex_used) in enumerate(series):
            at = base + timedelta(hours=offset - 1, minutes=10)
            session.add(weekly("claude-1", "anthropic", at, claude_used, reset))
            session.add(weekly("codex-1", "openai", at, codex_used, reset))
        session.add_all(
            [
                request(1, "claude-1", "anthropic", busy, "claude-opus-5-5", CLAUDE_NATIVE, 3.0, caller_seat="t3-code"),
                request(2, "claude-1", "anthropic", busy, "claude-opus-5-5", CHILD_NATIVE, 1.0, caller_seat="t3-code"),
                request(3, "claude-1", "anthropic", busy, "claude-haiku-5-5", "unknown-1", 1.0, caller_seat="t3-raise"),
                request(4, "claude-1", "anthropic", busy, "claude-opus-5-5", "elsewhere", 1.0, caller_seat="opus-seat"),
                request(5, "codex-1", "openai", busy, "gpt-6.1-sol", CLAUDE_NATIVE, 1.0, useragent_group="claude-cli"),
                request(
                    6,
                    "codex-1",
                    "openai",
                    busy,
                    "gpt-6.1-sol",
                    CODEX_NATIVE,
                    3.0,
                    useragent_group="T3",
                    cached_input_tokens=60,
                ),
            ]
        )
        await session.commit()


def keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys(item)}
    return set()


@pytest.mark.asyncio
async def test_sync_then_burn_names_threads_and_splits_the_weekly_rise(async_client):
    await seed()
    first = await async_client.post("/api/t3/threads/sync", json=sync_body())
    assert first.status_code == 200
    assert first.json() == {"threads": 4, "nativeSessions": 3, "movedSessions": 0}

    renamed = await async_client.post("/api/t3/threads/sync", json=sync_body("Recruiter app v2"))
    assert renamed.status_code == 200
    async with SessionLocal() as session:
        titles = (await session.execute(select(T3Thread.title).where(T3Thread.thread_id == ROOT))).scalars().all()
        assert titles == ["Recruiter app v2"]
        assert len((await session.execute(select(T3NativeSession))).scalars().all()) == 3

    burn = await async_client.get("/api/t3/threads/burn", params={"range": "24h"})
    assert burn.status_code == 200
    body = burn.json()
    assert not {key for key in keys(body) if "cost" in key.lower() or "usd" in key.lower()}
    assert body["windowHours"] == 24

    claude, codex = body["pools"]["anthropic"], body["pools"]["openai"]
    assert claude["t3Threads"]["accountWeekPct"] == pytest.approx(2 * 4 / 6)
    assert claude["t3Unregistered"]["accountWeekPct"] == pytest.approx(2 / 6)
    assert claude["outsideT3"]["accountWeekPct"] == pytest.approx(2 / 6)
    assert claude["matchedRequestPct"] == pytest.approx(100 * 2 / 3)
    assert codex["t3Threads"]["accountWeekPct"] == pytest.approx(4.0)
    assert codex["unexplained"]["accountWeekPct"] == pytest.approx(2.0)
    assert codex["t3Threads"]["poolSharePct"] == pytest.approx(4.0)

    rows = {row["rootThreadId"]: row for row in body["threads"]}
    assert set(rows) == {ROOT, CODEX}
    root = rows[ROOT]
    assert root["title"] == "Recruiter app v2"
    assert root["subagentCount"] == 1
    assert [child["threadId"] for child in root["children"]] == [CHILD]
    assert root["pools"]["anthropic"]["accountWeekPct"] == pytest.approx(2 * 4 / 6)
    assert root["pools"]["openai"]["accountWeekPct"] == pytest.approx(1.0)
    assert root["models"] == {"Opus": 2, "Sol via wrappers": 1}
    assert root["pools"]["openai"]["cacheReadTokens"] == 0
    assert rows[CODEX]["archived"] is True
    assert rows[CODEX]["nativeSessions"] == [{"nativeId": CODEX_NATIVE, "driver": "codex"}]
    assert rows[CODEX]["models"] == {"Codex": 1}
    codex_tokens = rows[CODEX]["pools"]["openai"]
    assert (codex_tokens["inputTokens"], codex_tokens["cacheReadTokens"]) == (40, 60)

    studio = (await async_client.get("/api/t3/threads/burn", params={"host": "studio"})).json()
    assert studio["host"] == "studio"
    assert studio["threads"] == []
    assert studio["pools"]["anthropic"]["t3Threads"]["accountWeekPct"] == 0
    assert studio["pools"]["anthropic"]["outsideT3"]["accountWeekPct"] == pytest.approx(2 / 6)

    one = (await async_client.get("/api/t3/threads/burn", params={"root": ROOT, "pool": "anthropic"})).json()
    assert [row["rootThreadId"] for row in one["threads"]] == [ROOT]
    assert sum(point["accountWeekPct"] for point in one["threads"][0]["hourly"]["anthropic"]) == pytest.approx(
        2 * 4 / 6
    )


@pytest.mark.asyncio
async def test_sync_rejects_a_bad_host_reports_a_moved_native_id_and_takes_the_last_duplicate(async_client):
    bad = await async_client.post("/api/t3/threads/sync", json={"host": "Bad Host", "threads": []})
    assert bad.status_code == 422

    await async_client.post("/api/t3/threads/sync", json=sync_body())
    moved = sync_body()
    moved["nativeSessions"][0]["threadId"] = CODEX
    again = await async_client.post("/api/t3/threads/sync", json=moved)
    assert again.json()["movedSessions"] == 1

    duplicated = sync_body()
    duplicated["threads"].append(thread(ROOT, "Recruiter app renamed", ROOT))
    duplicated["nativeSessions"].append({"nativeId": CHILD_NATIVE, "threadId": ROOT, "driver": "claudeAgent"})
    deduped = await async_client.post("/api/t3/threads/sync", json=duplicated)
    assert deduped.status_code == 200
    async with SessionLocal() as session:
        child = await session.get(T3NativeSession, CHILD_NATIVE)
        assert child is not None and child.thread_id == ROOT
        titles = (await session.execute(select(T3Thread.title).where(T3Thread.thread_id == ROOT))).scalars().all()
        assert titles == ["Recruiter app renamed"]
