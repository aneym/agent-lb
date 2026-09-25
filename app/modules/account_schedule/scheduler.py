from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from app.core.scheduling.leader_election import get_leader_election
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.db.session import get_background_session
from app.dependencies import get_accounts_context
from app.modules.account_schedule.repository import AccountScheduleRepository
from app.modules.accounts.service import AccountStateTransitionError

logger = logging.getLogger(__name__)


async def resume_due_accounts_once() -> None:
    if not await get_leader_election().try_acquire():
        return
    async with get_background_session() as session:
        schedules = AccountScheduleRepository(session)
        for schedule in await schedules.list_due(utcnow()):
            try:
                context = get_accounts_context(session)
                account = await context.repository.reload_by_id(schedule.account_id)
                if account is not None and account.status == AccountStatus.PAUSED:
                    await context.service.reactivate_account(schedule.account_id)
                await schedules.clear(schedule.account_id, resume_at=schedule.resume_at)
            except AccountStateTransitionError:
                # The state changed while this tick was running. Retry next tick.
                logger.info("Account resume raced with another state change: %s", schedule.account_id)
            except Exception:
                logger.exception("Failed to resume account %s", schedule.account_id)
                await session.rollback()


@dataclass(slots=True)
class AccountResumeScheduler:
    interval_seconds: int = 30
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await resume_due_accounts_once()
            except Exception:
                logger.exception("Account resume scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue


def build_account_resume_scheduler() -> AccountResumeScheduler:
    return AccountResumeScheduler()
