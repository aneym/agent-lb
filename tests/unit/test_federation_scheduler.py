from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.modules.federation.scheduler import FederationMirrorScheduler
from app.modules.proxy.account_cache import get_account_selection_cache

pytestmark = pytest.mark.unit


class _PeerClient:
    async def fetch_mirror(self, *, peer_url: str, token: str):
        del peer_url, token
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


class _Repo:
    def __init__(self, *, applied: bool) -> None:
        self.applied = applied

    async def upsert_mirror_account(self, **kwargs) -> bool:
        del kwargs
        return self.applied


def _repo_factory(*, applied: bool):
    @asynccontextmanager
    async def factory():
        yield _Repo(applied=applied)

    return factory


def _scheduler(*, applied: bool) -> FederationMirrorScheduler:
    return FederationMirrorScheduler(
        interval_seconds=60,
        enabled=True,
        peer_url="https://studio.example",
        federation_token="federation-token",
        local_instance_id="macbook",
        repo_factory=_repo_factory(applied=applied),
        peer_client=_PeerClient(),
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
