from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.federation.exceptions import FederationPeerRequestError
from app.modules.federation.scheduler import FederationMirrorScheduler, _default_federation_repo_factory
from app.modules.federation.schemas import (
    CALLER_MACHINE_MAX_LENGTH,
    CALLER_MACHINE_SOURCE_MAX_LENGTH,
    CALLER_SEAT_MAX_LENGTH,
    CALLER_USER_MAX_LENGTH,
    CALLER_USER_SOURCE_MAX_LENGTH,
    ROOM_MAX_LENGTH,
    FederationRequestLogRow,
)
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


class _RequestLogPeer(_PeerClient):
    def __init__(self, *, fail_logs: bool = False) -> None:
        super().__init__()
        self.fail_logs = fail_logs
        self.batches: list[list[int]] = []
        self.rows: list[FederationRequestLogRow] = []

    async def push_request_logs(self, *, peer_url: str, token: str, body) -> None:
        del peer_url, token
        if self.fail_logs:
            raise FederationPeerRequestError("Federation peer request failed status=500", status_code=500)
        self.batches.append([row.source_row_id for row in body.rows])
        self.rows.extend(body.rows)


def _log_repo_factory():
    # The production factory: its background session expires ORM rows on its
    # exit rollback, which a plain SessionLocal factory hid (edge crash 2026-10-04).
    return _default_federation_repo_factory


async def _seed_forward_logs() -> list[int]:
    now = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        session.add(
            Account(
                id="edge-acct",
                provider="anthropic",
                email="edge-acct@example.com",
                plan_type="claude",
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                last_refresh=now,
                status=AccountStatus.ACTIVE,
            )
        )
        session.add(
            RequestLog(
                account_id="edge-acct",
                request_id="old",
                model="claude-opus",
                status="success",
                requested_at=now - timedelta(hours=49),
            )
        )
        for index in range(501):
            session.add(
                RequestLog(
                    account_id="edge-acct",
                    request_id=f"fresh-{index}",
                    model="claude-opus",
                    status="success",
                    requested_at=now,
                    room="lab",
                    caller_seat="alex",
                )
            )
        session.add(
            RequestLog(
                account_id="edge-acct",
                request_id="deleted",
                model="claude-opus",
                status="success",
                requested_at=now,
                deleted_at=now,
            )
        )
        await session.commit()
        rows = (
            (
                await session.execute(
                    select(RequestLog.id)
                    .where(RequestLog.deleted_at.is_(None), RequestLog.request_id != "old")
                    .order_by(RequestLog.id)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


@pytest.mark.asyncio
async def test_request_log_forward_sends_id_order_batches_and_advances_cursor(db_setup, tmp_path, monkeypatch) -> None:
    del db_setup
    fresh_ids = await _seed_forward_logs()
    peer = _RequestLogPeer()
    cursor_path = tmp_path / "federation-request-log-cursor.json"
    scheduler = FederationMirrorScheduler(
        interval_seconds=60,
        enabled=True,
        peer_url="https://studio.example",
        federation_token="federation-token",
        local_instance_id="ax42",
        repo_factory=_log_repo_factory(),
        peer_client=peer,
        forward_request_logs=True,
        request_log_cursor_path=cursor_path,
    )
    monkeypatch.setattr("app.modules.federation.scheduler._REQUEST_LOG_BATCHES_PER_CYCLE", 1)

    await scheduler._forward_request_logs()

    assert peer.batches == [fresh_ids[:500]]
    assert json.loads(cursor_path.read_text()) == {"cursor": fresh_ids[499]}

    monkeypatch.setattr("app.modules.federation.scheduler._REQUEST_LOG_BATCHES_PER_CYCLE", 10)
    await scheduler._forward_request_logs()

    assert peer.batches == [fresh_ids[:500], fresh_ids[500:]]
    assert json.loads(cursor_path.read_text()) == {"cursor": fresh_ids[-1]}
    sent = [row_id for batch in peer.batches for row_id in batch]
    assert sent == fresh_ids


@pytest.mark.asyncio
async def test_request_log_forward_5xx_keeps_cursor_and_mirror_succeeds(
    db_setup, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_setup
    now = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                request_id="pending",
                model="claude-opus",
                status="success",
                requested_at=now,
            )
        )
        await session.commit()
    cursor_path = tmp_path / "federation-request-log-cursor.json"
    cursor_path.write_text(json.dumps({"cursor": 0}))
    peer = _RequestLogPeer(fail_logs=True)
    scheduler = FederationMirrorScheduler(
        interval_seconds=60,
        enabled=True,
        peer_url="https://studio.example",
        federation_token="federation-token",
        local_instance_id="ax42",
        repo_factory=_log_repo_factory(),
        peer_client=peer,
        forward_request_logs=True,
        request_log_cursor_path=cursor_path,
    )

    async def stop_wait_for(awaitable, *, timeout: float):
        awaitable.close()
        scheduler._stop.set()

    monkeypatch.setattr(asyncio, "wait_for", stop_wait_for)
    await scheduler._run_loop()

    assert json.loads(cursor_path.read_text()) == {"cursor": 0}
    assert scheduler.consecutive_failures == 0
    assert scheduler.last_success_at is not None
    assert scheduler.last_error is None
    assert len(peer.reports) == 1


@pytest.mark.asyncio
async def test_request_log_forward_truncates_bounded_caller_fields(db_setup, tmp_path) -> None:
    del db_setup
    now = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                request_id="long-fields",
                model="claude-opus",
                status="success",
                requested_at=now,
                caller_user="u" * (CALLER_USER_MAX_LENGTH + 8),
                caller_user_source="s" * (CALLER_USER_SOURCE_MAX_LENGTH + 8),
                caller_machine="m" * (CALLER_MACHINE_MAX_LENGTH + 8),
                caller_machine_source="c" * (CALLER_MACHINE_SOURCE_MAX_LENGTH + 8),
                caller_seat="e" * (CALLER_SEAT_MAX_LENGTH + 8),
                room="r" * (ROOM_MAX_LENGTH + 8),
            )
        )
        await session.commit()
        row_id = (
            await session.execute(select(RequestLog.id).where(RequestLog.request_id == "long-fields"))
        ).scalar_one()
    with pytest.raises(ValidationError):
        FederationRequestLogRow(
            source_row_id=row_id,
            provider="openai",
            request_id="long-fields",
            request_kind="normal",
            requested_at=now,
            model="claude-opus",
            status="success",
            room="r" * (ROOM_MAX_LENGTH + 1),
        )
    cursor_path = tmp_path / "federation-request-log-cursor.json"
    peer = _RequestLogPeer()
    scheduler = FederationMirrorScheduler(
        interval_seconds=60,
        enabled=True,
        peer_url="https://studio.example",
        federation_token="federation-token",
        local_instance_id="ax42",
        repo_factory=_log_repo_factory(),
        peer_client=peer,
        forward_request_logs=True,
        request_log_cursor_path=cursor_path,
    )

    await scheduler._forward_request_logs()

    assert len(peer.rows) == 1
    sent = peer.rows[0]
    assert sent.caller_user is not None and len(sent.caller_user) == CALLER_USER_MAX_LENGTH
    assert sent.caller_user_source is not None and len(sent.caller_user_source) == CALLER_USER_SOURCE_MAX_LENGTH
    assert sent.caller_machine is not None and len(sent.caller_machine) == CALLER_MACHINE_MAX_LENGTH
    assert sent.caller_machine_source is not None and len(sent.caller_machine_source) == CALLER_MACHINE_SOURCE_MAX_LENGTH
    assert sent.caller_seat is not None and len(sent.caller_seat) == CALLER_SEAT_MAX_LENGTH
    assert sent.room is not None and len(sent.room) == ROOM_MAX_LENGTH
    assert json.loads(cursor_path.read_text()) == {"cursor": row_id}


@pytest.mark.asyncio
async def test_request_log_forward_resets_cursor_ahead_of_table(db_setup, tmp_path, caplog) -> None:
    del db_setup
    now = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                request_id="stale-old",
                model="claude-opus",
                status="success",
                requested_at=now - timedelta(hours=49),
            )
        )
        session.add(
            RequestLog(
                request_id="stale-recent",
                model="claude-opus",
                status="success",
                requested_at=now - timedelta(hours=1),
            )
        )
        await session.commit()
        recent_id = (
            await session.execute(select(RequestLog.id).where(RequestLog.request_id == "stale-recent"))
        ).scalar_one()
    cursor_path = tmp_path / "federation-request-log-cursor.json"
    cursor_path.write_text(json.dumps({"cursor": recent_id + 10_000}))
    peer = _RequestLogPeer()
    scheduler = FederationMirrorScheduler(
        interval_seconds=60,
        enabled=True,
        peer_url="https://studio.example",
        federation_token="federation-token",
        local_instance_id="ax42",
        repo_factory=_log_repo_factory(),
        peer_client=peer,
        forward_request_logs=True,
        request_log_cursor_path=cursor_path,
    )

    with caplog.at_level(logging.WARNING, logger="app.modules.federation.scheduler"):
        await scheduler._forward_request_logs()

    assert any("resetting to the 48h window" in record.message for record in caplog.records)
    assert [row.request_id for row in peer.rows] == ["stale-recent"]
    assert json.loads(cursor_path.read_text()) == {"cursor": recent_id}
