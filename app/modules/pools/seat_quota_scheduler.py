from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import sys
from pathlib import Path

from app.modules.pools.grok_bot import collect_grok_bot_usage

logger = logging.getLogger(__name__)


class SeatQuotaScheduler:
    def __init__(self) -> None:
        self.interval = max(1, float(os.environ.get("AGENT_LB_SEAT_QUOTA_INTERVAL_S", "300")))
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.interval * random.uniform(0.9, 1.1))

    async def refresh(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            installed = Path.home() / ".agent-lb/bin/seat"
            binary = (
                Path(os.environ["AGENT_LB_SEAT_BIN"]).expanduser()
                if os.environ.get("AGENT_LB_SEAT_BIN")
                else (installed if installed.is_file() else Path(__file__).resolve().parents[3] / "clients/seat")
            )
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(binary),
                    "quota",
                    "--json",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(process.wait(), timeout=60)
                if process.returncode:
                    logger.warning("Seat quota collector exited with status %s", process.returncode)
            except (OSError, asyncio.TimeoutError):
                logger.warning("Seat quota collector failed or timed out")
            finally:
                if process is not None and process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            try:
                await asyncio.to_thread(collect_grok_bot_usage)
            except OSError:
                logger.warning("Grok Bot usage collector failed")


def build_seat_quota_scheduler() -> SeatQuotaScheduler:
    return SeatQuotaScheduler()
