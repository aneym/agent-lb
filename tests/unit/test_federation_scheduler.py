from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.modules.federation.scheduler import FederationMirrorScheduler
from app.modules.proxy.account_cache import get_account_selection_cache

pytestmark = pytest.mark.unit


class _PeerClient:
    def __init__(self, *, fetch_error: Exception | None = None, push_error: Exception | None = None) -> None:
        self.fetch_error = fetch_error
        self.push_error = push_error
        self.reports = []
        self.scheduler_at_push: FederationMirrorScheduler | None = None
        self.mirror_success_at_push = None

    async def fetch_mirror(self, *, peer_url: str, token: str):
        del peer_url, token
        if self.fetch_error is not None:
            raise self.fetch_error
        account = SimpleNamespace(
            account_id="mirrored-openai",
            provider="openai",
            email="mirror@example.com",
            alias="mirror",
            status="active",
            plan_type="plus",
            chatgpt_account_id="chatgpt-account",
            access_token="fresh-access-token",
        )
        return SimpleNamespace(instance_id="studio", accounts=[account])

    async def push_usage_report(self, *, peer_url: str, token: str, report) -> None:
        del peer_url, token
        self.reports.append(report)
        if self.scheduler_at_push is not None:
            self.mirror_success_at_push = self.scheduler_at_push.last_success_at
        if self.push_error is not None:
            raise self.push_error


class _Repo:
    def __init__(self, *, applied: bool) -> None:
        self.applied = applied

    async def upsert_mirror_account(self, **kwargs) -> bool:
        del kwargs
        return self.applied

    async def list_local_usage_rollups(self, *, window_days: int):
        assert window_days == 7
        return []


def _repo_factory(*, applied: bool):
    @asynccontextmanager
    async def factory():
        yield _Repo(applied=applied)

    return factory


def _scheduler(*, applied: bool, peer_client: _PeerClient | None = None) -> FederationMirrorScheduler:
    return FederationMirrorScheduler(
        interval_seconds=60,
        enabled=True,
        peer_url="https://studio.example",
        federation_token="federation-token",
        local_instance_id="macbook",
        repo_factory=_repo_factory(applied=applied),
        peer_client=peer_client or _PeerClient(),
    )


@pytest.mark.asyncio
async def test_mirror_pull_invalidates_cached_empty_selection() -> None:
    cache = get_account_selection_cache()
    before = cache.generation

    await _scheduler(applied=True).mirror_once()

    assert cache.generation == before + 1


@pytest.mark.asyncio
async def test_noop_mirror_pull_preserves_selection_cache() -> None:
    cache = get_account_selection_cache()
    before = cache.generation

    await _scheduler(applied=False).mirror_once()

    assert cache.generation == before


@pytest.mark.asyncio
async def test_usage_push_failure_does_not_fail_mirror_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    peer = _PeerClient(push_error=RuntimeError("owner unavailable"))
    scheduler = _scheduler(applied=True, peer_client=peer)
    peer.scheduler_at_push = scheduler

    async def stop_wait_for(awaitable, *, timeout: float):
        awaitable.close()
        scheduler._stop.set()

    monkeypatch.setattr(asyncio, "wait_for", stop_wait_for)
    await scheduler._run_loop()

    assert len(peer.reports) == 1
    assert peer.mirror_success_at_push is not None
    assert scheduler.last_success_at == peer.mirror_success_at_push
    assert scheduler.consecutive_failures == 0
    assert scheduler.last_error is None
    assert scheduler.usage_push_last_success_at is None
    assert scheduler.usage_push_last_error == "owner unavailable"


@pytest.mark.asyncio
async def test_run_loop_updates_failure_health(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = _scheduler(applied=False, peer_client=_PeerClient(fetch_error=RuntimeError("peer down")))

    async def stop_after_failure(_seconds: float) -> None:
        scheduler._stop.set()

    scheduler.sleep = stop_after_failure
    async def stop_wait_for(awaitable, *, timeout: float):
        awaitable.close()
        await scheduler.sleep(timeout)

    monkeypatch.setattr(asyncio, "wait_for", stop_wait_for)
    await scheduler._run_loop()

    assert scheduler.last_attempt_at is not None
    assert scheduler.last_success_at is None
    assert scheduler.consecutive_failures == 1
    assert scheduler.last_error == "peer down"
