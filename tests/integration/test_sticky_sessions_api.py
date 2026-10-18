from __future__ import annotations

import pytest

from app.db.models import StickySessionKind
from tests.integration.sticky_session_support import (
    _create_accounts,
    _insert_sticky_session,
    _set_affinity_ttl,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_sticky_sessions_api_lists_metadata_and_purges_stale(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="prompt-cache-stale",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="prompt-cache-fresh",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=10,
    )
    await _insert_sticky_session(
        key="codex-session-old",
        account_id=accounts[1].id,
        kind=StickySessionKind.CODEX_SESSION,
        updated_at_offset_seconds=600,
    )

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    payload = response.json()
    entries = {entry["key"]: entry for entry in payload["entries"]}
    assert payload["total"] == 3
    assert payload["hasMore"] is False

    assert entries["prompt-cache-stale"]["kind"] == "prompt_cache"
    assert entries["prompt-cache-stale"]["displayName"] == "sticky-a@example.com"
    assert entries["prompt-cache-stale"]["isStale"] is True
    assert entries["prompt-cache-stale"]["expiresAt"] is not None
    assert entries["prompt-cache-fresh"]["displayName"] == "sticky-a@example.com"
    assert entries["prompt-cache-fresh"]["isStale"] is False
    assert entries["codex-session-old"]["kind"] == "codex_session"
    assert entries["codex-session-old"]["displayName"] == "sticky-b@example.com"
    assert entries["codex-session-old"]["isStale"] is False
    assert entries["codex-session-old"]["expiresAt"] is None

    response = await async_client.get("/api/sticky-sessions", params={"staleOnly": "true"})
    assert response.status_code == 200
    stale_payload = response.json()
    assert [entry["key"] for entry in stale_payload["entries"]] == ["prompt-cache-stale"]
    assert stale_payload["total"] == 1
    assert stale_payload["hasMore"] is False

    response = await async_client.post("/api/sticky-sessions/purge", json={"staleOnly": True})
    assert response.status_code == 200
    assert response.json()["deletedCount"] == 1

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    remaining_keys = {entry["key"] for entry in response.json()["entries"]}
    assert remaining_keys == {"prompt-cache-fresh", "codex-session-old"}


@pytest.mark.asyncio
async def test_sticky_sessions_api_filters_by_account_and_key(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="alpha-thread-1",
        account_id=accounts[0].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=5,
    )
    await _insert_sticky_session(
        key="alpha-thread-2",
        account_id=accounts[1].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=10,
    )
    await _insert_sticky_session(
        key="beta-cache",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=15,
    )

    response = await async_client.get("/api/sticky-sessions", params={"accountQuery": "sticky-a"})
    assert response.status_code == 200
    assert {(entry["key"], entry["displayName"]) for entry in response.json()["entries"]} == {
        ("alpha-thread-1", "sticky-a@example.com"),
        ("beta-cache", "sticky-a@example.com"),
    }
    assert response.json()["total"] == 2

    response = await async_client.get("/api/sticky-sessions", params={"keyQuery": "alpha-thread"})
    assert response.status_code == 200
    assert {(entry["key"], entry["displayName"]) for entry in response.json()["entries"]} == {
        ("alpha-thread-1", "sticky-a@example.com"),
        ("alpha-thread-2", "sticky-b@example.com"),
    }
    assert response.json()["total"] == 2

    response = await async_client.get(
        "/api/sticky-sessions",
        params={"accountQuery": "sticky-a", "keyQuery": "beta"},
    )
    assert response.status_code == 200
    assert [(entry["key"], entry["displayName"]) for entry in response.json()["entries"]] == [
        ("beta-cache", "sticky-a@example.com")
    ]
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_sticky_sessions_api_sorts_by_supported_fields(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="sort-z",
        account_id=accounts[1].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=20,
    )
    await _insert_sticky_session(
        key="sort-a",
        account_id=accounts[0].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=10,
    )
    await _insert_sticky_session(
        key="sort-m",
        account_id=accounts[0].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=30,
    )

    response = await async_client.get("/api/sticky-sessions", params={"sortBy": "key", "sortDir": "asc"})
    assert response.status_code == 200
    assert [entry["key"] for entry in response.json()["entries"]] == ["sort-a", "sort-m", "sort-z"]

    response = await async_client.get("/api/sticky-sessions", params={"sortBy": "account", "sortDir": "asc"})
    assert response.status_code == 200
    assert [entry["displayName"] for entry in response.json()["entries"]] == [
        "sticky-a@example.com",
        "sticky-a@example.com",
        "sticky-b@example.com",
    ]

    response = await async_client.get("/api/sticky-sessions", params={"sortBy": "updated_at", "sortDir": "asc"})
    assert response.status_code == 200
    assert [entry["key"] for entry in response.json()["entries"]] == ["sort-m", "sort-z", "sort-a"]


@pytest.mark.asyncio
async def test_sticky_sessions_api_rejects_non_stale_purge_requests(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="prompt-cache-stale",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="codex-session-old",
        account_id=accounts[1].id,
        kind=StickySessionKind.CODEX_SESSION,
        updated_at_offset_seconds=600,
    )

    response = await async_client.post("/api/sticky-sessions/purge", json={"staleOnly": False})
    assert response.status_code == 422

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stalePromptCacheCount"] == 1
    assert {entry["key"] for entry in payload["entries"]} == {"prompt-cache-stale", "codex-session-old"}


@pytest.mark.asyncio
async def test_sticky_sessions_api_applies_offset_before_returning_page(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)

    for index in range(15):
        await _insert_sticky_session(
            key=f"page-session-{index:02d}",
            account_id=accounts[index % len(accounts)].id,
            kind=StickySessionKind.STICKY_THREAD,
            updated_at_offset_seconds=index + 1,
        )

    response = await async_client.get("/api/sticky-sessions", params={"limit": "10", "offset": "10"})
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 15
    assert payload["hasMore"] is False
    assert len(payload["entries"]) == 5
