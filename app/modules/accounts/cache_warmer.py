from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from app.core.config.settings import get_settings
from app.modules.accounts.service import with_background_accounts_service

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AccountsCacheWarmer:
    """Keeps GET /api/accounts off cold loads.

    ``start`` fills the accounts read caches (additional quotas and request usage)
    before the app takes traffic, so the first request after a blue/green swap is a
    cache hit, then reloads the additional quotas every ``interval_seconds`` so a
    one-shot reader (cc banner, route pools) never sees a value older than that.
    Request usage stays stale-while-revalidate: only the dashboard reads it.
    """

    interval_seconds: float
    startup_timeout_seconds: float
    enabled: bool
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
            return
        try:
            await asyncio.wait_for(self._warm(include_request_usage=True), timeout=self.startup_timeout_seconds)
        except Exception:
            logger.warning("Accounts cache startup warm failed; the first reads will load inline", exc_info=True)
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
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                return
            try:
                await self._warm(include_request_usage=False)
            except Exception:
                logger.warning("Accounts cache refresh failed", exc_info=True)

    async def _warm(self, *, include_request_usage: bool) -> None:
        await with_background_accounts_service(
            lambda service: service.warm_read_caches(include_request_usage=include_request_usage)
        )


def build_accounts_cache_warmer() -> AccountsCacheWarmer:
    settings = get_settings()
    return AccountsCacheWarmer(
        interval_seconds=settings.accounts_cache_warmer_interval_seconds,
        startup_timeout_seconds=settings.accounts_cache_warmer_startup_timeout_seconds,
        enabled=settings.accounts_cache_warmer_enabled,
    )
