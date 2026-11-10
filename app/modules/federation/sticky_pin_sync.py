from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.federation.peer_client import AiohttpFederationPeerClient, FederationPeerClient
from app.modules.federation.sticky_pin_schemas import (
    STICKY_PIN_BATCH_LIMIT,
    StickyPinExchangeRequest,
    StickyPinExchangeResponse,
)
from app.modules.federation.sticky_pin_store import StickyPinStore

logger = logging.getLogger(__name__)

SEED_MARKER_NAME = "federation-sticky-pin-seed.json"
_INTERVAL_SECONDS = 10.0
_START_TIMEOUT_SECONDS = 5.0
_STOP_TIMEOUT_SECONDS = 3.0
_RESTART_LOOKBACK = timedelta(hours=6)
_OVERLAP = timedelta(seconds=60)
_FAILURE_BACKOFF_MAX_SECONDS = 300.0


@dataclass(slots=True)
class StickyPinSync:
    peer_url: str
    token: str
    local_instance_id: str
    seed_marker: Path
    peer_client: FederationPeerClient = field(default_factory=AiohttpFederationPeerClient)
    sent_through: datetime | None = None
    received_through: datetime | None = None
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_error: str | None = None
    _task: asyncio.Task[None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        await self._bounded_exchange(_START_TIMEOUT_SECONDS)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._bounded_exchange(_STOP_TIMEOUT_SECONDS)

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._delay())
            await self._bounded_exchange(None)

    def _delay(self) -> float:
        if not self.consecutive_failures:
            return _INTERVAL_SECONDS
        return min(_FAILURE_BACKOFF_MAX_SECONDS, _INTERVAL_SECONDS * 2 ** min(self.consecutive_failures, 5))

    async def _bounded_exchange(self, timeout: float | None) -> None:
        try:
            await asyncio.wait_for(self.exchange_once(), timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = type(exc).__name__
            logger.warning(
                "Federation sticky pin exchange failed error_type=%s consecutive_failures=%s",
                type(exc).__name__,
                self.consecutive_failures,
            )
            return
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_at = utcnow()

    async def exchange_once(self) -> None:
        async with self._lock:
            started = utcnow()
            seeding = not self.seed_marker.exists()
            sent = await self._push_local(started, seeding=seeding)
            received, server_now = await self._pull_peer(started)
            self.sent_through = started
            self.received_through = server_now
            if seeding:
                self.seed_marker.write_text(json.dumps({"seeded_at": started.isoformat(), "pins": sent}))
            if sent or received:
                logger.info(
                    "Federation sticky pins exchanged sent=%s applied_from_peer=%s seed=%s", sent, received, seeding
                )

    async def _push_local(self, started: datetime, *, seeding: bool) -> int:
        since = None if seeding else (self.sent_through or started - _RESTART_LOOKBACK) - _OVERLAP
        after_key: str | None = None
        sent = 0
        while True:
            async with get_background_session() as session:
                pins = await StickyPinStore(session).list_pins(
                    since=since, after_key=after_key, limit=STICKY_PIN_BATCH_LIMIT
                )
            if not pins:
                return sent
            await self._post(StickyPinExchangeRequest(instance_id=self.local_instance_id, seed=seeding, pins=pins))
            sent += len(pins)
            if len(pins) < STICKY_PIN_BATCH_LIMIT:
                return sent
            after_key = pins[-1].key

    async def _pull_peer(self, started: datetime) -> tuple[int, datetime]:
        since = (self.received_through or started - _RESTART_LOOKBACK) - _OVERLAP
        after_key: str | None = None
        applied = 0
        server_now: datetime | None = None
        while True:
            response = await self._post(
                StickyPinExchangeRequest(instance_id=self.local_instance_id, since=since, after_key=after_key)
            )
            server_now = server_now or response.server_now
            if response.pins:
                async with get_background_session() as session:
                    applied += await StickyPinStore(session).apply(response.pins, seed=False)
            if not response.more:
                return applied, server_now
            after_key = response.pins[-1].key

    async def _post(self, body: StickyPinExchangeRequest) -> StickyPinExchangeResponse:
        return await self.peer_client.exchange_sticky_pins(peer_url=self.peer_url, token=self.token, body=body)
