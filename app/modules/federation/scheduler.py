from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import get_background_session
from app.modules.federation.peer_client import AiohttpFederationPeerClient, FederationPeerClient
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import (
    CALLER_MACHINE_MAX_LENGTH,
    CALLER_MACHINE_SOURCE_MAX_LENGTH,
    CALLER_SEAT_MAX_LENGTH,
    CALLER_USER_MAX_LENGTH,
    CALLER_USER_SOURCE_MAX_LENGTH,
    ROOM_MAX_LENGTH,
    FederationRequestLogRow,
    FederationRequestLogsRequest,
    FederationUsageReportRequest,
)
from app.modules.proxy.account_cache import get_account_selection_cache

logger = logging.getLogger(__name__)

_FAILURE_BACKOFF_BASE_SECONDS = 30.0
_FAILURE_BACKOFF_MAX_SECONDS = 1800.0
_REQUEST_LOG_BATCH_SIZE = 500
_REQUEST_LOG_BATCHES_PER_CYCLE = 10
_REQUEST_LOG_LOOKBACK = timedelta(hours=48)
_REQUEST_LOG_CURSOR_NAME = "federation-request-log-cursor.json"

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
    usage_window_days: int = 7
    forward_request_logs: bool = True
    request_log_cursor_path: Path | None = None
    peer_client: FederationPeerClient = field(default_factory=AiohttpFederationPeerClient)
    encryptor: TokenEncryptor = field(default_factory=TokenEncryptor)
    sleep: Callable[[float], Awaitable[None]] = field(default_factory=lambda: asyncio.sleep)
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: str | None = None
    usage_push_last_success_at: datetime | None = None
    usage_push_last_error: str | None = None

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
                self.last_attempt_at = utcnow()
                await self.mirror_once()
                self.consecutive_failures = 0
                self.last_success_at = utcnow()
                self.last_error = None
            except Exception as exc:
                self.consecutive_failures += 1
                self.last_error = str(exc)
                delay = min(
                    _FAILURE_BACKOFF_MAX_SECONDS,
                    _FAILURE_BACKOFF_BASE_SECONDS * (2 ** min(self.consecutive_failures - 1, 6)),
                )
                logger.warning(
                    "Federation mirror pull failed consecutive_failures=%s retry_in_seconds=%.0f",
                    self.consecutive_failures,
                    delay,
                    exc_info=True,
                )
            else:
                await self._push_usage_report()
                await self._forward_request_logs()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def mirror_once(self) -> None:
        if not self.peer_url or not self.federation_token:
            return
        response = await self.peer_client.fetch_mirror(peer_url=self.peer_url, token=self.federation_token)
        applied = False
        async with self.repo_factory() as repo:
            for account in response.accounts:
                applied = (
                    await repo.upsert_mirror_account(
                        account_id=account.account_id,
                        provider=account.provider,
                        email=account.email,
                        alias=account.alias,
                        status=account.status,
                        plan_type=account.plan_type,
                        chatgpt_account_id=account.chatgpt_account_id,
                        access_token=account.access_token,
                        expires_at_ms=getattr(account, "expires_at_ms", None),
                        owner_instance_id=response.instance_id,
                        local_instance_id=self.local_instance_id,
                        encryptor=self.encryptor,
                    )
                    or applied
                )
        if applied:
            # A request can cache an empty selection before the first mirror
            # pull completes. Make newly mirrored (or refreshed) accounts
            # routable immediately instead of preserving that stale 503 until
            # the selection cache expires or the process restarts.
            get_account_selection_cache().invalidate()

    async def _push_usage_report(self) -> None:
        if not self.peer_url or not self.federation_token:
            return
        try:
            async with self.repo_factory() as repo:
                rollups = await repo.list_local_usage_rollups(window_days=self.usage_window_days)
            await self.peer_client.push_usage_report(
                peer_url=self.peer_url,
                token=self.federation_token,
                report=FederationUsageReportRequest(instance_id=self.local_instance_id, rollups=rollups),
            )
        except Exception as exc:
            self.usage_push_last_error = str(exc)
            logger.warning("Federation usage report push failed; mirror pull remains successful", exc_info=True)
            return
        self.usage_push_last_success_at = utcnow()
        self.usage_push_last_error = None

    async def _forward_request_logs(self) -> None:
        if not self.forward_request_logs or self.request_log_cursor_path is None:
            return
        if not self.peer_url or not self.federation_token:
            return
        try:
            cursor = await self._cursor_for_forward()
            for _ in range(_REQUEST_LOG_BATCHES_PER_CYCLE):
                since = None if cursor is not None else utcnow() - _REQUEST_LOG_LOOKBACK
                # Snapshot the rows inside the session: the background session
                # expires ORM rows when it rolls back on exit, and reading log.id afterwards
                # raised on every edge (2026-10-04 00:05Z, ax42).
                async with self.repo_factory() as repo:
                    logs = await repo.list_request_logs_to_forward(
                        after_id=cursor,
                        since=since,
                        limit=_REQUEST_LOG_BATCH_SIZE,
                    )
                    rows = [_request_log_row(log) for log in logs]
                if not rows:
                    return
                body = FederationRequestLogsRequest(instance_id=self.local_instance_id, rows=rows)
                await self.peer_client.push_request_logs(
                    peer_url=self.peer_url,
                    token=self.federation_token,
                    body=body,
                )
                cursor = max(row.source_row_id for row in rows)
                _write_request_log_cursor(self.request_log_cursor_path, cursor)
                if len(rows) < _REQUEST_LOG_BATCH_SIZE:
                    return
        except Exception:
            logger.warning("Federation request-log forward failed; mirror pull remains successful", exc_info=True)

    async def _cursor_for_forward(self) -> int | None:
        """Return the cursor, or None (48h window) when it sits past the table.

        A restored or replaced store can reuse low ids while the cursor file
        still names an id from the previous file. Leaving that cursor in place
        would skip every current row forever.
        """
        assert self.request_log_cursor_path is not None
        cursor = _read_request_log_cursor(self.request_log_cursor_path)
        if cursor is None:
            return None
        async with self.repo_factory() as repo:
            max_id = await repo.max_request_log_id()
        if max_id is not None and cursor <= max_id:
            return cursor
        logger.warning(
            "Federation request-log cursor %s is ahead of max request_logs id %s; resetting to the 48h window",
            cursor,
            max_id,
        )
        _clear_request_log_cursor(self.request_log_cursor_path)
        return None


def _request_log_row(log: RequestLog) -> FederationRequestLogRow:
    return FederationRequestLogRow(
        source_row_id=log.id,
        account_id=log.account_id,
        provider=log.provider,
        api_key_id=log.api_key_id,
        session_id=log.session_id,
        client_session_id=log.client_session_id,
        caller_user=_clip(log.caller_user, CALLER_USER_MAX_LENGTH),
        caller_user_source=_clip(log.caller_user_source, CALLER_USER_SOURCE_MAX_LENGTH),
        caller_machine=_clip(log.caller_machine, CALLER_MACHINE_MAX_LENGTH),
        caller_machine_source=_clip(log.caller_machine_source, CALLER_MACHINE_SOURCE_MAX_LENGTH),
        caller_seat=_clip(log.caller_seat, CALLER_SEAT_MAX_LENGTH),
        room=_clip(log.room, ROOM_MAX_LENGTH),
        unified_5h_utilization=log.unified_5h_utilization,
        unified_7d_utilization=log.unified_7d_utilization,
        request_id=log.request_id,
        request_kind=log.request_kind,
        requested_at=log.requested_at,
        model=log.model,
        plan_type=log.plan_type,
        source=log.source,
        useragent=log.useragent,
        useragent_group=log.useragent_group,
        transport=log.transport,
        service_tier=log.service_tier,
        requested_service_tier=log.requested_service_tier,
        actual_service_tier=log.actual_service_tier,
        input_tokens=log.input_tokens,
        output_tokens=log.output_tokens,
        cached_input_tokens=log.cached_input_tokens,
        cache_creation_tokens=log.cache_creation_tokens,
        cache_read_tokens=log.cache_read_tokens,
        reasoning_tokens=log.reasoning_tokens,
        cost_usd=log.cost_usd,
        reasoning_effort=log.reasoning_effort,
        latency_ms=log.latency_ms,
        latency_first_token_ms=log.latency_first_token_ms,
        status=log.status,
        error_code=log.error_code,
        error_message=log.error_message,
        failure_phase=log.failure_phase,
        failure_detail=log.failure_detail,
        failure_exception_type=log.failure_exception_type,
        upstream_status_code=log.upstream_status_code,
        upstream_error_code=log.upstream_error_code,
        bridge_stage=log.bridge_stage,
        upstream_proxy_route_mode=log.upstream_proxy_route_mode,
        upstream_proxy_pool_id=log.upstream_proxy_pool_id,
        upstream_proxy_endpoint_id=log.upstream_proxy_endpoint_id,
        upstream_proxy_fallback_used=log.upstream_proxy_fallback_used,
        upstream_proxy_fail_closed_reason=log.upstream_proxy_fail_closed_reason,
    )


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]


def _read_request_log_cursor(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cursor = payload.get("cursor") if isinstance(payload, dict) else None
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        return None
    return cursor


def _clear_request_log_cursor(path: Path) -> None:
    path.unlink(missing_ok=True)


def _write_request_log_cursor(path: Path, cursor: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"cursor": cursor}), encoding="utf-8")
    os.replace(temporary, path)


@asynccontextmanager
async def _default_federation_repo_factory() -> AsyncIterator[FederationRepository]:
    async with get_background_session() as session:
        yield FederationRepository(session)


def build_federation_mirror_scheduler() -> FederationMirrorScheduler:
    settings = get_settings()
    return FederationMirrorScheduler(
        interval_seconds=settings.federation_mirror_interval_seconds,
        enabled=bool(settings.federation_peer_url and settings.effective_federation_mirror_token),
        peer_url=settings.federation_peer_url,
        federation_token=settings.effective_federation_mirror_token,
        local_instance_id=settings.local_instance_id,
        usage_window_days=settings.federation_usage_window_days,
        forward_request_logs=settings.federation_forward_request_logs,
        request_log_cursor_path=settings.data_dir / _REQUEST_LOG_CURSOR_NAME,
        repo_factory=_default_federation_repo_factory,
    )
