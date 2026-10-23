from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import Float, Result, bindparam, delete, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.db.session import get_background_session
from app.db.settings_models import SchedulerLeader

logger = logging.getLogger(__name__)
_T = TypeVar("_T")
_MAX_CONSECUTIVE_RENEW_ERRORS = 2
_CANCEL_GRACE_SECONDS = 5.0
_RELEASE_DRAIN_GRACE_SECONDS = 5.0
_POSTGRES_ACQUIRE_SQL = text(
    "\n    INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at)\n    VALUES (1, :leader_id, clock_timestamp(), clock_timestamp() + make_interval(secs => :ttl))\n    ON CONFLICT (id) DO UPDATE SET\n        leader_id = excluded.leader_id,\n        acquired_at = clock_timestamp(),\n        expires_at = clock_timestamp() + make_interval(secs => :ttl)\n    WHERE scheduler_leader.expires_at < now() OR scheduler_leader.leader_id = :leader_id\n    RETURNING extract(epoch FROM (expires_at - clock_timestamp()))\n    "
).bindparams(bindparam("ttl", type_=Float))
_POSTGRES_RENEW_SQL = text(
    "\n    UPDATE scheduler_leader\n    SET expires_at = clock_timestamp() + make_interval(secs => :ttl)\n    WHERE id = 1 AND leader_id = :leader_id AND expires_at > clock_timestamp()\n    RETURNING extract(epoch FROM (expires_at - clock_timestamp()))\n    "
).bindparams(bindparam("ttl", type_=Float))
_SQLITE_NOW = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"
_SQLITE_NOW_PLUS_TTL = "(strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || :ttl || ' seconds') || '000')"
_SQLITE_REMAINING = "((julianday(expires_at) - julianday('now')) * 86400.0)"
_SQLITE_ACQUIRE_SQL = text(
    f"\n    INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at)\n    VALUES (1, :leader_id, {_SQLITE_NOW}, {_SQLITE_NOW_PLUS_TTL})\n    ON CONFLICT (id) DO UPDATE SET\n        leader_id = excluded.leader_id,\n        acquired_at = {_SQLITE_NOW},\n        expires_at = {_SQLITE_NOW_PLUS_TTL}\n    WHERE scheduler_leader.expires_at < {_SQLITE_NOW} OR scheduler_leader.leader_id = :leader_id\n    RETURNING {_SQLITE_REMAINING}\n    "
).bindparams(bindparam("ttl", type_=Float))
_SQLITE_RENEW_SQL = text(
    f"\n    UPDATE scheduler_leader\n    SET expires_at = {_SQLITE_NOW_PLUS_TTL}\n    WHERE id = 1 AND leader_id = :leader_id AND expires_at > {_SQLITE_NOW}\n    RETURNING {_SQLITE_REMAINING}\n    "
).bindparams(bindparam("ttl", type_=Float))


def _dialect_name(session: AsyncSession) -> str:
    return session.get_bind().dialect.name


def _is_locked_error(exc: BaseException) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    message = str(exc.orig if exc.orig is not None else exc).lower()
    return "database is locked" in message or "database is busy" in message


def _returned_remaining(result: Result[Any]) -> float | None:
    row = result.first()
    if row is None:
        return None
    return float(row[0])


class LeaderElection:
    def __init__(self, leader_id: str | None = None) -> None:
        self._leader_id = leader_id or str(uuid.uuid4())
        self._is_leader = False
        self._lease_deadline: float | None = None
        self._detached_bodies: set[asyncio.Task[Any]] = set()
        self._abandoned_renewals: set[asyncio.Task[bool]] = set()
        self._release_keeper: asyncio.Task[None] | None = None
        self._release_keeper_stop: asyncio.Event | None = None

    @property
    def leader_id(self) -> str:
        return self._leader_id

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    async def try_acquire(self) -> bool:
        settings = get_settings()
        if not settings.leader_election_enabled:
            self._is_leader = True
            return True
        ttl = settings.leader_election_ttl_seconds
        loop = asyncio.get_running_loop()
        try:
            async with get_background_session() as session:
                acquire_sql = _SQLITE_ACQUIRE_SQL if _dialect_name(session) == "sqlite" else _POSTGRES_ACQUIRE_SQL
                result = await session.execute(acquire_sql, {"leader_id": self._leader_id, "ttl": ttl})
                remaining = _returned_remaining(result)
                acquired_at = loop.time()
                if remaining is None:
                    self._is_leader = False
                    self._lease_deadline = None
                    try:
                        await session.commit()
                    except Exception:
                        logger.warning(
                            "Leader lease acquisition observed a non-owner result (no row) but the commit failed; treating the lease as not acquired leader_id=%s",
                            self._leader_id,
                            exc_info=True,
                        )
                    return False
                await session.commit()
        except Exception:
            if self._is_leader and self._lease_deadline is not None and (loop.time() < self._lease_deadline):
                logger.warning(
                    "Leader lease acquisition failed but the held lease is still valid; preserving leadership leader_id=%s",
                    self._leader_id,
                    exc_info=True,
                )
                return True
            logger.warning("Leader election failed, defaulting to non-leader", exc_info=True)
            self._is_leader = False
            self._lease_deadline = None
            return False
        self._is_leader = True
        self._lease_deadline = acquired_at + remaining
        return True

    async def renew(self) -> bool:
        if not self._is_leader:
            return False
        settings = get_settings()
        if not settings.leader_election_enabled:
            return True
        ttl = settings.leader_election_ttl_seconds
        loop = asyncio.get_running_loop()
        async with get_background_session() as session:
            renew_sql = _SQLITE_RENEW_SQL if _dialect_name(session) == "sqlite" else _POSTGRES_RENEW_SQL
            result = await session.execute(renew_sql, {"leader_id": self._leader_id, "ttl": ttl})
            remaining = _returned_remaining(result)
            renew_anchor = loop.time()
            if remaining is None:
                self._is_leader = False
                self._lease_deadline = None
                try:
                    await session.commit()
                except Exception:
                    logger.warning(
                        "Leader lease renewal observed lease loss (rowcount 0) but the commit failed; treating the lease as lost leader_id=%s",
                        self._leader_id,
                        exc_info=True,
                    )
                return False
            await session.commit()
        self._lease_deadline = renew_anchor + remaining
        return True

    async def release(self) -> None:
        self._is_leader = False
        self._lease_deadline = None
        settings = get_settings()
        if not settings.leader_election_enabled:
            await self._stop_release_keeper()
            return
        await self._stop_release_keeper()
        if not await self._drain_detached_bodies():
            logger.warning(
                "Skipping early leader lease release: detached leader-gated work is still draining after %.1fs; the lease will expire after its TTL",
                _RELEASE_DRAIN_GRACE_SECONDS,
            )
            return
        try:
            async with get_background_session() as session:
                await session.execute(
                    delete(SchedulerLeader).where(SchedulerLeader.id == 1, SchedulerLeader.leader_id == self._leader_id)
                )
                await session.commit()
        except Exception as exc:
            if _is_locked_error(exc):
                logger.debug(
                    "Leader lease release contended on a locked database; leaving the row to expire after its TTL leader_id=%s",
                    self._leader_id,
                    exc_info=True,
                )
                return
            logger.warning("Failed to release scheduler leader lease", exc_info=True)

    def start_release_keeper(self) -> None:
        if not get_settings().leader_election_enabled:
            return
        if self._release_keeper is not None and (not self._release_keeper.done()):
            return
        stop = asyncio.Event()
        self._release_keeper_stop = stop
        self._release_keeper = asyncio.create_task(self._run_release_keeper(stop))

    async def _run_release_keeper(self, stop: asyncio.Event) -> None:
        renew_interval = max(1, get_settings().leader_election_ttl_seconds // 3)
        try:
            while not stop.is_set():
                await self._renew_lease_row()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=renew_interval)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _stop_release_keeper(self) -> None:
        keeper = self._release_keeper
        stop = self._release_keeper_stop
        self._release_keeper = None
        self._release_keeper_stop = None
        if keeper is None:
            return
        if stop is not None:
            stop.set()
        keeper.cancel()
        try:
            await keeper
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Scheduler leader lease keeper failed during shutdown", exc_info=True)

    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None:
        if not await self.try_acquire():
            return None
        settings = get_settings()
        if not settings.leader_election_enabled:
            return await fn()
        ttl = settings.leader_election_ttl_seconds
        renew_interval = max(1, ttl // 3)
        renew_timeout = max(1.0, ttl / 6)
        loop = asyncio.get_running_loop()
        lease_deadline = self._lease_deadline if self._lease_deadline is not None else loop.time() + ttl
        body_task: asyncio.Task[_T] = asyncio.ensure_future(fn())
        lease_lost = False

        async def _heartbeat() -> None:
            nonlocal lease_deadline, lease_lost
            consecutive_errors = 0
            inflight: asyncio.Task[bool] | None = None
            try:
                while True:
                    if self._lease_deadline is not None and self._lease_deadline > lease_deadline:
                        lease_deadline = self._lease_deadline
                    remaining = lease_deadline - loop.time()
                    if remaining <= 0:
                        self._is_leader = False
                        self._lease_deadline = None
                        lease_lost = True
                        return
                    sleep_budget = min(float(renew_interval), max(0.0, remaining - renew_timeout))
                    await asyncio.sleep(sleep_budget)
                    inflight = asyncio.ensure_future(self.renew())
                    renew_wait = min(renew_timeout, max(0.0, lease_deadline - loop.time()))
                    done, _ = await asyncio.wait({inflight}, timeout=renew_wait)
                    renew_task = inflight
                    inflight = None
                    try:
                        if renew_task not in done:
                            self._abandon_renewal(renew_task)
                            raise TimeoutError("leader lease renewal timed out")
                        renewed = renew_task.result()
                    except Exception:
                        consecutive_errors += 1
                        logger.warning(
                            "Leader lease renewal errored or timed out consecutive_errors=%s",
                            consecutive_errors,
                            exc_info=True,
                        )
                        if consecutive_errors < _MAX_CONSECUTIVE_RENEW_ERRORS and loop.time() < lease_deadline:
                            continue
                        if (
                            self._is_leader
                            and self._lease_deadline is not None
                            and (self._lease_deadline > lease_deadline)
                        ):
                            lease_deadline = self._lease_deadline
                            consecutive_errors = 0
                            continue
                        self._is_leader = False
                        self._lease_deadline = None
                        renewed = False
                    else:
                        consecutive_errors = 0
                        if renewed and self._lease_deadline is not None:
                            lease_deadline = self._lease_deadline
                    if not renewed:
                        lease_lost = True
                        return
            finally:
                if inflight is not None and (not inflight.done()):
                    self._abandon_renewal(inflight)

        heartbeat_task = asyncio.create_task(_heartbeat())
        body_cancel_handled = False
        try:
            done, _ = await asyncio.wait({body_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat_task in done and (not lease_lost) and (not body_task.done()):
                logger.error(
                    "Leader heartbeat failed unexpectedly; demoting leader_id=%s",
                    self._leader_id,
                    exc_info=heartbeat_task.exception(),
                )
                self._is_leader = False
                self._lease_deadline = None
                lease_lost = True
            if lease_lost:
                logger.warning("Leader-gated task cancelled after lease loss leader_id=%s", self._leader_id)
                body_cancel_handled = True
                await self._cancel_within_grace(body_task)
                return None
            return await body_task
        finally:
            if not body_cancel_handled and (not body_task.done()):
                await self._cancel_within_grace(body_task)
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Leader heartbeat failed during cleanup")

    async def _cancel_within_grace(self, task: asyncio.Task[Any]) -> None:
        task.cancel()
        _, pending = await asyncio.wait({task}, timeout=_CANCEL_GRACE_SECONDS)
        if pending:
            self._detached_bodies.add(task)
            task.add_done_callback(self._on_detached_body_done)
            logger.warning(
                "Leader-gated task still draining shielded work %.1fs after cancellation; detaching",
                _CANCEL_GRACE_SECONDS,
            )
            return
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Leader-gated task failed while being cancelled", exc_info=exc)

    def _abandon_renewal(self, task: asyncio.Task[bool]) -> None:
        self._abandoned_renewals.add(task)
        task.add_done_callback(self._on_renewal_done)
        task.cancel()

    def _on_renewal_done(self, task: asyncio.Task[bool]) -> None:
        self._abandoned_renewals.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("Abandoned leader lease renewal finished with error", exc_info=exc)

    def _on_detached_body_done(self, task: asyncio.Task[Any]) -> None:
        self._detached_bodies.discard(task)
        _log_detached_body_result(task)

    async def _drain_detached_bodies(self) -> bool:
        pending = {task for task in self._detached_bodies if not task.done()}
        if not pending:
            return True
        settings = get_settings()
        renew_interval = max(1, settings.leader_election_ttl_seconds // 3)
        loop = asyncio.get_running_loop()
        logger.info(
            "Waiting up to %.1fs for %d detached leader-gated task(s), renewing the lease meanwhile, before releasing it",
            _RELEASE_DRAIN_GRACE_SECONDS,
            len(pending),
        )
        deadline = loop.time() + _RELEASE_DRAIN_GRACE_SECONDS
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await self._renew_lease_row()
            _, pending = await asyncio.wait(pending, timeout=min(float(renew_interval), remaining))
        return True

    async def _renew_lease_row(self) -> bool:
        settings = get_settings()
        ttl = settings.leader_election_ttl_seconds
        try:
            async with get_background_session() as session:
                renew_sql = _SQLITE_RENEW_SQL if _dialect_name(session) == "sqlite" else _POSTGRES_RENEW_SQL
                result = await session.execute(renew_sql, {"leader_id": self._leader_id, "ttl": ttl})
                renewed = _returned_remaining(result) is not None
                await session.commit()
                return renewed
        except Exception as exc:
            if _is_locked_error(exc):
                logger.debug(
                    "Leader lease renewal contended on a locked database; will retry next cadence leader_id=%s",
                    self._leader_id,
                    exc_info=True,
                )
                return False
            logger.warning(
                "Failed to renew leader lease while draining detached bodies leader_id=%s",
                self._leader_id,
                exc_info=True,
            )
            return False


def _log_detached_body_result(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        logger.info("Detached leader-gated task finished cancelling after lease loss")
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Detached leader-gated task failed after lease loss", exc_info=exc)
    else:
        logger.info("Detached leader-gated task completed after lease loss")


_leader_election: LeaderElection | None = None


def get_leader_election() -> LeaderElection:
    global _leader_election
    if _leader_election is None:
        _leader_election = LeaderElection()
    return _leader_election
