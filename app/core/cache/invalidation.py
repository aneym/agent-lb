from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    cache_invalidation_bump_failures_total,
    cache_invalidation_poll_failures_total,
)
from app.db.session import close_session
from app.db.settings_models import CacheInvalidation

if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
NAMESPACE_API_KEY = "api_key"
NAMESPACE_FIREWALL = "firewall"
NAMESPACE_ACCOUNT_ROUTING = "account_routing"
NAMESPACE_ACCOUNT_SELECTION = "account_selection"
NAMESPACE_SETTINGS = "settings"
NAMESPACE_RESET_CREDITS = "reset_credits"
NAMESPACE_MODEL_REGISTRY = "model_registry"
NAMESPACE_UPSTREAM_ROUTE = "upstream_route"
type InvalidationCallback = Callable[[], object | Awaitable[object]]
_NAMESPACE_LOG_LABELS: dict[str, str] = {
    "api_key": "api_key",
    "firewall": "firewall",
    "account_routing": "account_routing",
    "account_selection": "account_selection",
    "settings": "settings",
    "reset_credits": "reset_credits",
    "model_registry": "model_registry",
    "upstream_route": "upstream_route",
}
_BUMP_RETRY_ATTEMPTS = 3
_BUMP_RETRY_BASE_SECONDS = 0.05
_POLL_FAILURES_WARNING_THRESHOLD = 3
_POLL_FAILURES_ERROR_THRESHOLD = 10


class CacheInvalidationPoller:
    def __init__(self, session_factory: Callable[[], AsyncSession], poll_interval_seconds: float = 0.5) -> None:
        self._session_factory = session_factory
        self._poll_interval = poll_interval_seconds
        self._known_versions: dict[str, int] = {}
        self._callbacks: dict[str, list[InvalidationCallback]] = {}
        self._pending_bumps: set[str] = set()
        self._consecutive_poll_failures = 0
        self._poll_initialized = False
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def on_invalidation(self, namespace: str, callback: InvalidationCallback) -> None:
        self._callbacks.setdefault(namespace, []).append(callback)

    async def initialize(self) -> None:
        session = self._session_factory()
        try:
            result = await session.execute(select(CacheInvalidation.namespace, CacheInvalidation.version))
            rows = result.all()
        finally:
            await close_session(session)
        for namespace, version in rows:
            self._known_versions[namespace] = version
        self._poll_initialized = True

    async def prime(self) -> None:
        if not await self._poll_once():
            raise RuntimeError("cache invalidation prime failed: baseline version read did not complete")

    async def start(self) -> None:
        if self._task and (not self._task.done()):
            return
        self._poll_initialized = True
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def request_bump(self, namespace: str) -> None:
        self._pending_bumps.add(namespace)

    async def bump(self, namespace: str) -> bool:
        for attempt in range(_BUMP_RETRY_ATTEMPTS):
            try:
                await self._bump_once(namespace)
                return True
            except OperationalError:
                if attempt == _BUMP_RETRY_ATTEMPTS - 1:
                    self._record_bump_failure(namespace)
                    return False
                await asyncio.sleep(_BUMP_RETRY_BASE_SECONDS * 2**attempt)
            except Exception:
                self._record_bump_failure(namespace)
                return False
        return False

    async def bump_local(self, namespace: str) -> bool:
        if not await self.bump(namespace):
            return False
        session = self._session_factory()
        try:
            version = await session.scalar(
                select(CacheInvalidation.version).where(CacheInvalidation.namespace == namespace)
            )
        except Exception:
            logger.debug("cache_invalidation bump_local acknowledge read failed", exc_info=True)
            return True
        finally:
            await close_session(session)
        if version is not None:
            self._known_versions[namespace] = max(self._known_versions.get(namespace, 0), version)
        return True

    async def _bump_once(self, namespace: str) -> None:
        session = self._session_factory()
        try:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                stmt = (
                    pg_insert(CacheInvalidation)
                    .values(namespace=namespace, version=1)
                    .on_conflict_do_update(
                        index_elements=[CacheInvalidation.namespace], set_={"version": CacheInvalidation.version + 1}
                    )
                )
                await session.execute(stmt)
            elif dialect == "sqlite":
                stmt = (
                    sqlite_insert(CacheInvalidation)
                    .values(namespace=namespace, version=1)
                    .on_conflict_do_update(
                        index_elements=[CacheInvalidation.namespace], set_={"version": CacheInvalidation.version + 1}
                    )
                )
                await session.execute(stmt)
            else:
                existing = await session.scalar(
                    select(CacheInvalidation).where(CacheInvalidation.namespace == namespace)
                )
                if existing is None:
                    session.add(CacheInvalidation(namespace=namespace, version=1))
                else:
                    await session.execute(
                        update(CacheInvalidation)
                        .where(CacheInvalidation.namespace == namespace)
                        .values(version=CacheInvalidation.version + 1)
                    )
            await session.commit()
        finally:
            await close_session(session)

    def _record_bump_failure(self, namespace: str) -> None:
        logger.error(
            "cache_invalidation bump failed for namespace %s",
            _NAMESPACE_LOG_LABELS.get(namespace, "unknown"),
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE and cache_invalidation_bump_failures_total is not None:
            cache_invalidation_bump_failures_total.labels(namespace=namespace).inc()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception:
                logger.debug("cache_invalidation poll failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                continue

    async def _flush_pending_bumps(self) -> None:
        for namespace in sorted(self._pending_bumps):
            self._pending_bumps.discard(namespace)
            try:
                if not await self.bump(namespace):
                    self._pending_bumps.add(namespace)
            except asyncio.CancelledError:
                self._pending_bumps.add(namespace)
                raise
            except Exception:
                self._pending_bumps.add(namespace)
                logger.warning(
                    "cache_invalidation flush bump raised for namespace %s; kept pending",
                    _NAMESPACE_LOG_LABELS.get(namespace, "unknown"),
                    exc_info=True,
                )

    async def _poll_once(self) -> bool:
        await self._flush_pending_bumps()
        session: AsyncSession | None = None
        try:
            session = self._session_factory()
            result = await session.execute(select(CacheInvalidation.namespace, CacheInvalidation.version))
            rows = result.all()
        except Exception:
            self._record_poll_failure()
            return False
        finally:
            if session is not None:
                await close_session(session)
        self._consecutive_poll_failures = 0
        for namespace, version in rows:
            prev = self._known_versions.get(namespace)
            changed = prev is not None and version > prev or (prev is None and self._poll_initialized and (version > 0))
            if changed and (not await self._run_callbacks(namespace)):
                continue
            self._known_versions[namespace] = max(self._known_versions.get(namespace, 0), version)
        self._poll_initialized = True
        return True

    async def _run_callbacks(self, namespace: str) -> bool:
        succeeded = True
        for cb in self._callbacks.get(namespace, []):
            try:
                result = cb()
                if isawaitable(result):
                    await result
            except Exception:
                succeeded = False
                logger.warning(
                    "cache_invalidation callback failed for namespace %s; retrying next poll cycle",
                    _NAMESPACE_LOG_LABELS.get(namespace, "unknown"),
                    exc_info=True,
                )
        return succeeded

    def _record_poll_failure(self) -> None:
        self._consecutive_poll_failures += 1
        if PROMETHEUS_AVAILABLE and cache_invalidation_poll_failures_total is not None:
            cache_invalidation_poll_failures_total.inc()
        if self._consecutive_poll_failures >= _POLL_FAILURES_ERROR_THRESHOLD:
            logger.error(
                "cache_invalidation poll failed %d consecutive times", self._consecutive_poll_failures, exc_info=True
            )
        elif self._consecutive_poll_failures >= _POLL_FAILURES_WARNING_THRESHOLD:
            logger.warning(
                "cache_invalidation poll failed %d consecutive times", self._consecutive_poll_failures, exc_info=True
            )
        else:
            logger.debug("cache_invalidation poll failed", exc_info=True)


_poller: CacheInvalidationPoller | None = None


def get_cache_invalidation_poller() -> CacheInvalidationPoller | None:
    return _poller


def set_cache_invalidation_poller(poller: CacheInvalidationPoller | None) -> None:
    global _poller
    _poller = poller


async def bump_cache_invalidation(namespace: str) -> None:
    poller = _poller
    if poller is None:
        return
    await poller.bump(namespace)


async def bump_cache_invalidation_local(namespace: str) -> None:
    poller = _poller
    if poller is None:
        return
    await poller.bump_local(namespace)
