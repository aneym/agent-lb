from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import pytest
from sqlalchemy import text

from app.core.utils.time import utcnow
from app.db.models import StickySessionKind
from app.db.session import SessionLocal
from tests.integration.sticky_session_support import (
    _create_accounts,
    _insert_sticky_session,
    _set_affinity_ttl,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_sticky_sessions_api_deletes_filtered_rows(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="cleanup-alpha-1",
        account_id=accounts[0].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=5,
    )
    await _insert_sticky_session(
        key="cleanup-alpha-2",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=10,
    )
    await _insert_sticky_session(
        key="cleanup-beta",
        account_id=accounts[1].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=15,
    )

    response = await async_client.post(
        "/api/sticky-sessions/delete-filtered",
        json={"accountQuery": "sticky-a", "keyQuery": "cleanup-alpha"},
    )
    assert response.status_code == 200
    assert response.json() == {"deletedCount": 2}

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    assert {(entry["key"], entry["displayName"]) for entry in response.json()["entries"]} == {
        ("cleanup-beta", "sticky-b@example.com")
    }


@pytest.mark.asyncio
async def test_sticky_sessions_api_counts_hidden_stale_rows_and_deletes_by_kind(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)

    for index in range(101):
        await _insert_sticky_session(
            key=f"fresh-session-{index:03d}",
            account_id=accounts[index % len(accounts)].id,
            kind=StickySessionKind.STICKY_THREAD,
            updated_at_offset_seconds=index + 1,
        )

    await _insert_sticky_session(
        key="shared-key",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="shared-key",
        account_id=accounts[1].id,
        kind=StickySessionKind.CODEX_SESSION,
        updated_at_offset_seconds=5,
    )

    response = await async_client.get("/api/sticky-sessions", params={"limit": "10", "offset": "0"})
    assert response.status_code == 200
    payload = response.json()

    assert payload["stalePromptCacheCount"] == 1
    assert payload["total"] == 103
    assert payload["hasMore"] is True
    assert len(payload["entries"]) == 10
    assert not any(entry["key"] == "shared-key" and entry["kind"] == "prompt_cache" for entry in payload["entries"])
    assert any(entry["key"] == "shared-key" and entry["kind"] == "codex_session" for entry in payload["entries"])

    response = await async_client.get(
        "/api/sticky-sessions", params={"staleOnly": "true", "limit": "10", "offset": "0"}
    )
    assert response.status_code == 200
    stale_payload = response.json()
    assert stale_payload["stalePromptCacheCount"] == 1
    assert stale_payload["total"] == 1
    assert stale_payload["hasMore"] is False
    assert [(entry["key"], entry["kind"]) for entry in stale_payload["entries"]] == [("shared-key", "prompt_cache")]

    response = await async_client.delete("/api/sticky-sessions/prompt_cache/shared-key")
    assert response.status_code == 200

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    after_delete = response.json()
    assert after_delete["stalePromptCacheCount"] == 0
    assert after_delete["total"] == 102
    assert any(entry["key"] == "shared-key" and entry["kind"] == "codex_session" for entry in after_delete["entries"])


@pytest.mark.asyncio
async def test_sticky_sessions_api_deletes_selected_identifiers(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)

    await _insert_sticky_session(
        key="shared-key",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="shared-key",
        account_id=accounts[1].id,
        kind=StickySessionKind.CODEX_SESSION,
        updated_at_offset_seconds=5,
    )
    await _insert_sticky_session(
        key="folder/session",
        account_id=accounts[0].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=5,
    )

    response = await async_client.post(
        "/api/sticky-sessions/delete",
        json={
            "sessions": [
                {"key": "shared-key", "kind": "prompt_cache"},
                {"key": "folder/session", "kind": "sticky_thread"},
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["deletedCount"] == 2
    assert response.json()["deleted"] == [
        {"key": "shared-key", "kind": "prompt_cache"},
        {"key": "folder/session", "kind": "sticky_thread"},
    ]
    assert response.json()["failed"] == []

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    remaining = {(entry["key"], entry["kind"]) for entry in response.json()["entries"]}
    assert remaining == {("shared-key", "codex_session")}


@pytest.mark.asyncio
async def test_sticky_sessions_api_reports_partial_failures_for_bulk_delete(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)

    await _insert_sticky_session(
        key="shared-key",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )

    response = await async_client.post(
        "/api/sticky-sessions/delete",
        json={
            "sessions": [
                {"key": "shared-key", "kind": "prompt_cache"},
                {"key": "missing-key", "kind": "sticky_thread"},
            ]
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "deletedCount": 1,
        "deleted": [
            {"key": "shared-key", "kind": "prompt_cache"},
        ],
        "failed": [
            {"key": "missing-key", "kind": "sticky_thread", "reason": "not_found"},
        ],
    }


@pytest.mark.asyncio
async def test_sticky_sessions_api_rejects_duplicate_bulk_delete_targets(async_client):
    response = await async_client.post(
        "/api/sticky-sessions/delete",
        json={
            "sessions": [
                {"key": "shared-key", "kind": "prompt_cache"},
                {"key": "shared-key", "kind": "prompt_cache"},
            ]
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Invalid request payload",
        }
    }


@pytest.mark.asyncio
async def test_sticky_sessions_api_deletes_slash_containing_keys(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    sticky_key = "folder/session"

    await _insert_sticky_session(
        key=sticky_key,
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=10,
    )

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    assert any(entry["key"] == sticky_key for entry in response.json()["entries"])

    response = await async_client.delete(f"/api/sticky-sessions/prompt_cache/{quote(sticky_key, safe='')}")
    assert response.status_code == 200

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    assert all(entry["key"] != sticky_key for entry in response.json()["entries"])


@pytest.mark.asyncio
async def test_sticky_sessions_api_deletes_filtered_chunks_large_match_set(async_client):

    accounts = await _create_accounts()
    await _set_affinity_ttl(60)

    bulk_count = 600
    async with SessionLocal() as session:
        for i in range(bulk_count):
            await session.execute(
                text(
                    """
                    INSERT INTO sticky_sessions (key, account_id, kind, created_at, updated_at)
                    VALUES (:key, :account_id, :kind, :timestamp, :timestamp)
                    """
                ),
                {
                    "key": f"bulk-delete-{i:04d}",
                    "account_id": accounts[0].id,
                    "kind": StickySessionKind.STICKY_THREAD.value,
                    "timestamp": utcnow() - timedelta(seconds=i),
                },
            )
        await session.commit()

    response = await async_client.post(
        "/api/sticky-sessions/delete-filtered",
        json={"accountQuery": "sticky-a", "keyQuery": "bulk-delete-"},
    )
    assert response.status_code == 200
    assert response.json() == {"deletedCount": bulk_count}

    async with SessionLocal() as session:
        remaining = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM sticky_sessions WHERE key LIKE 'bulk-delete-%'",
                )
            )
        ).scalar_one()
    assert remaining == 0
