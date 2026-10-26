from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.request_models import RequestLog
from app.db.session import sqlite_writer_section
from app.db.t3_models import T3NativeSession, T3Thread
from app.modules.t3_threads.attribution import (
    CACHE_CREATION_WEIGHT,
    CACHE_READ_WEIGHT,
    HOUR_SECONDS,
    OUTPUT_WEIGHT,
)
from app.modules.t3_threads.schemas import SyncNativeSession, SyncThread

logger = logging.getLogger(__name__)

UPSERT_CHUNK = 200
POOL_PROVIDERS = ("anthropic", "openai")
THREAD_FIELDS = (
    "project_id",
    "project_title",
    "workspace_root",
    "title",
    "provider_instance",
    "model",
    "parent_thread_id",
    "root_thread_id",
    "relationship",
    "created_by",
    "creation_source",
    "created_at",
    "updated_at",
    "archived_at",
    "deleted_at",
)


@dataclass(frozen=True)
class RequestGroup:
    account_id: str
    provider: str
    hour: int
    session: str | None
    model: str
    seat: str | None
    ua_group: str | None
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost: float
    priced_equivalents: float
    unpriced_equivalents: float
    last_at: datetime


def _insert(session: AsyncSession):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return pg_insert
    if dialect == "sqlite":
        return sqlite_insert
    raise RuntimeError(f"T3 registry upsert unsupported for dialect={dialect!r}")


class T3ThreadsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        host: str,
        threads: Sequence[SyncThread],
        sessions: Sequence[SyncNativeSession],
        now: datetime,
    ) -> int:
        insert_fn = _insert(self._session)
        threads = list({thread.thread_id: thread for thread in threads}.values())
        sessions = list({item.native_id: item for item in sessions}.values())
        moved = await self._moved_sessions(host, sessions)
        async with sqlite_writer_section(self._session):
            for start in range(0, len(threads), UPSERT_CHUNK):
                rows = [
                    {
                        "host": host,
                        "thread_id": thread.thread_id,
                        "first_seen_at": now,
                        "last_seen_at": now,
                        **{name: getattr(thread, name) for name in THREAD_FIELDS},
                    }
                    for thread in threads[start : start + UPSERT_CHUNK]
                ]
                statement = insert_fn(T3Thread).values(rows)
                await self._session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[T3Thread.host, T3Thread.thread_id],
                        set_={name: statement.excluded[name] for name in (*THREAD_FIELDS, "last_seen_at")},
                    )
                )
            for start in range(0, len(sessions), UPSERT_CHUNK):
                rows = [
                    {
                        "native_id": item.native_id,
                        "host": host,
                        "thread_id": item.thread_id,
                        "driver": item.driver,
                        "provider_instance": item.provider_instance,
                        "first_seen_at": now,
                        "last_seen_at": now,
                    }
                    for item in sessions[start : start + UPSERT_CHUNK]
                ]
                statement = insert_fn(T3NativeSession).values(rows)
                await self._session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[T3NativeSession.native_id],
                        set_={
                            name: statement.excluded[name]
                            for name in ("host", "thread_id", "driver", "provider_instance", "last_seen_at")
                        },
                    )
                )
            await self._session.commit()
        return moved

    async def _moved_sessions(self, host: str, sessions: Sequence[SyncNativeSession]) -> int:
        moved = 0
        for start in range(0, len(sessions), UPSERT_CHUNK):
            chunk = {item.native_id: item for item in sessions[start : start + UPSERT_CHUNK]}
            existing = await self._session.execute(
                select(T3NativeSession.native_id, T3NativeSession.host, T3NativeSession.thread_id).where(
                    T3NativeSession.native_id.in_(chunk)
                )
            )
            for native_id, old_host, old_thread in existing.all():
                if (old_host, old_thread) != (host, chunk[native_id].thread_id):
                    moved += 1
                    logger.warning(
                        "T3 native session moved to another thread",
                        extra={"native_id": native_id, "from_host": old_host, "to_host": host},
                    )
        return moved

    async def registry(self) -> tuple[list[T3Thread], list[T3NativeSession]]:
        threads = (await self._session.execute(select(T3Thread))).scalars().all()
        sessions = (await self._session.execute(select(T3NativeSession))).scalars().all()
        return list(threads), list(sessions)

    async def request_groups(self, start: datetime) -> list[RequestGroup]:
        if self._session.get_bind().dialect.name == "postgresql":
            bucket = func.floor(func.extract("epoch", RequestLog.requested_at) / HOUR_SECONDS)
        else:
            bucket = cast(cast(func.strftime("%s", RequestLog.requested_at), Integer) / HOUR_SECONDS, Integer)
        bucket = bucket.label("hour")
        raw_input = func.coalesce(RequestLog.input_tokens, 0)
        output_tokens = func.coalesce(RequestLog.output_tokens, 0)
        cache_creation = func.coalesce(RequestLog.cache_creation_tokens, 0)
        cache_read = func.coalesce(RequestLog.cache_read_tokens, RequestLog.cached_input_tokens, 0)
        input_tokens = case(
            (RequestLog.provider == "anthropic", raw_input),
            (raw_input > cache_read, raw_input - cache_read),
            else_=0,
        )
        equivalents = (
            input_tokens
            + OUTPUT_WEIGHT * output_tokens
            + CACHE_CREATION_WEIGHT * cache_creation
            + CACHE_READ_WEIGHT * cache_read
        )
        priced = RequestLog.cost_usd.is_not(None)
        statement = (
            select(
                RequestLog.account_id,
                RequestLog.provider,
                bucket,
                RequestLog.client_session_id,
                RequestLog.model,
                RequestLog.caller_seat,
                RequestLog.useragent_group,
                func.count().label("requests"),
                func.sum(input_tokens).label("input_tokens"),
                func.sum(output_tokens).label("output_tokens"),
                func.sum(cache_read).label("cache_read_tokens"),
                func.sum(func.coalesce(RequestLog.cost_usd, 0)).label("cost"),
                func.sum(case((priced, equivalents), else_=0)).label("priced"),
                func.sum(case((priced, 0), else_=equivalents)).label("unpriced"),
                func.max(RequestLog.requested_at).label("last_at"),
            )
            .where(
                RequestLog.requested_at >= start,
                RequestLog.deleted_at.is_(None),
                RequestLog.account_id.is_not(None),
                RequestLog.provider.in_(POOL_PROVIDERS),
            )
            .group_by(
                RequestLog.account_id,
                RequestLog.provider,
                bucket,
                RequestLog.client_session_id,
                RequestLog.model,
                RequestLog.caller_seat,
                RequestLog.useragent_group,
            )
        )
        return [
            RequestGroup(
                account_id=row.account_id,
                provider=row.provider,
                hour=int(row.hour),
                session=row.client_session_id,
                model=row.model,
                seat=row.caller_seat,
                ua_group=row.useragent_group,
                requests=int(row.requests),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cache_read_tokens=int(row.cache_read_tokens or 0),
                cost=float(row.cost or 0),
                priced_equivalents=float(row.priced or 0),
                unpriced_equivalents=float(row.unpriced or 0),
                last_at=row.last_at,
            )
            for row in (await self._session.execute(statement)).all()
        ]
