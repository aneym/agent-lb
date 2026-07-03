from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.session import get_background_session
from app.modules.federation.peer_client import AiohttpFederationPeerClient, FederationPeerClient
from app.modules.federation.repository import FederationRepository

logger = logging.getLogger(__name__)

_FAILURE_BACKOFF_BASE_SECONDS = 30.0
_FAILURE_BACKOFF_MAX_SECONDS = 1800.0

_RepoFactory = Callable[[], AbstractAsyncContextManager[FederationRepository]]


@dataclass(slots=True)
class FederationMirrorScheduler:
    """Non-owner mirror-pull loop: periodically imports the peer's owned-account

    access tokens. Never runs against an unconfigured peer; exponential
    backoff on failure, capped at ~30 minutes.
    """

    interval_seconds: int
    enabled: bool
    peer_url: str | None
    federation_token: str | None
    local_instance_id: str
    repo_factory: _RepoFactory
    peer_client: FederationPeerClient = field(default_factory=AiohttpFederationPeerClient)
    encryptor: TokenEncryptor = field(default_factory=TokenEncryptor)
    sleep: Callable[[float], Awaitable[None]] = field(default_factory=lambda: asyncio.sleep)
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _consecutive_failures: int = 0

    async def start(self) -> None:
        if not self.enabled or not self.peer_url or not self.federation_token:
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            delay = self.interval_seconds
            try:
                await self.mirror_once()
                self._consecutive_failures = 0
            except Exception:
                self._consecutive_failures += 1
                delay = min(
                    _FAILURE_BACKOFF_MAX_SECONDS,
                    _FAILURE_BACKOFF_BASE_SECONDS * (2 ** min(self._consecutive_failures - 1, 6)),
                )
                logger.warning(
                    "Federation mirror pull failed consecutive_failures=%s retry_in_seconds=%.0f",
                    self._consecutive_failures,
                    delay,
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def mirror_once(self) -> None:
        if not self.peer_url or not self.federation_token:
            return
        response = await self.peer_client.fetch_mirror(peer_url=self.peer_url, token=self.federation_token)
        async with self.repo_factory() as repo:
            for account in response.accounts:
                await repo.upsert_mirror_account(
                    account_id=account.account_id,
                    provider=account.provider,
                    email=account.email,
                    alias=account.alias,
                    status=account.status,
                    plan_type=account.plan_type,
                    chatgpt_account_id=account.chatgpt_account_id,
                    access_token=account.access_token,
                    owner_instance_id=response.instance_id,
                    local_instance_id=self.local_instance_id,
                    encryptor=self.encryptor,
                )


@asynccontextmanager
async def _default_federation_repo_factory() -> AsyncIterator[FederationRepository]:
    async with get_background_session() as session:
        yield FederationRepository(session)


def build_federation_mirror_scheduler() -> FederationMirrorScheduler:
    settings = get_settings()
    return FederationMirrorScheduler(
        interval_seconds=settings.federation_mirror_interval_seconds,
        enabled=bool(settings.federation_peer_url and settings.federation_token),
        peer_url=settings.federation_peer_url,
        federation_token=settings.federation_token,
        local_instance_id=settings.local_instance_id,
        repo_factory=_default_federation_repo_factory,
    )
