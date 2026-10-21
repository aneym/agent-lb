from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core import shutdown as shutdown_state
from app.core.auth.refresh import RefreshError
from app.db import session as db_session

REFRESH_LOCK_TIMEOUT_SECONDS = 30


def _refresh_lock_key(account_id: str) -> int:
    digest = sha256(f"account-refresh:{account_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


_REFRESH_LOCK_ENGINE: AsyncEngine | None = None


def _refresh_lock_engine() -> AsyncEngine | None:

    global _REFRESH_LOCK_ENGINE
    if db_session.engine.dialect.name != "postgresql":
        return None
    if _REFRESH_LOCK_ENGINE is None:
        _REFRESH_LOCK_ENGINE = create_async_engine(
            db_session.engine.url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=REFRESH_LOCK_TIMEOUT_SECONDS,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    return _REFRESH_LOCK_ENGINE


@asynccontextmanager
async def _cross_process_refresh_lock(account_id: str) -> AsyncIterator[None]:
    shutdown_state.begin_exchange()
    try:
        lock_engine = _refresh_lock_engine()
        if lock_engine is None:
            yield
            return
        async with lock_engine.connect() as conn, conn.begin():
            await conn.execute(text(f"SET LOCAL lock_timeout = '{REFRESH_LOCK_TIMEOUT_SECONDS}s'"))
            try:
                await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _refresh_lock_key(account_id)})
            except Exception as exc:
                raise RefreshError(
                    "refresh_lock_timeout",
                    f"Another process held the refresh lock for {account_id} over {REFRESH_LOCK_TIMEOUT_SECONDS}s",
                    False,
                    transport_error=True,
                ) from exc
            yield
    finally:
        shutdown_state.end_exchange()
