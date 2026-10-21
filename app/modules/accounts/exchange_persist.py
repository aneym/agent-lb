from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config.settings import get_settings
from app.db import session as db_session
from app.modules.accounts.repository import AccountsRepository

logger = logging.getLogger(__name__)

_PERSIST_BACKOFF_START_SECONDS = 0.5
_PERSIST_BACKOFF_MAX_SECONDS = 5.0

_dedicated_engine: AsyncEngine | None = None
_T = TypeVar("_T")


def persist_budget_seconds() -> float:
    return max(60.0, 3 * float(get_settings().database_pool_timeout_seconds))


def _get_dedicated_engine() -> AsyncEngine:
    global _dedicated_engine
    if _dedicated_engine is None:
        url = db_session.engine.url.render_as_string(hide_password=False)
        _dedicated_engine = create_async_engine(
            url,
            echo=False,
            poolclass=NullPool,
            connect_args=db_session._postgres_async_connect_args(url) or {},
        )
    return _dedicated_engine


@asynccontextmanager
async def dedicated_accounts_repo() -> AsyncIterator[AccountsRepository]:
    if db_session.engine.dialect.name != "postgresql":
        async with db_session.get_background_session() as session:
            yield AccountsRepository(session)
        return
    async with AsyncSession(_get_dedicated_engine(), expire_on_commit=False) as session:
        yield AccountsRepository(session)


async def _retry_on_fresh_repo(
    repo_factory: Callable[[], AbstractAsyncContextManager[Any]],
    operation: Callable[[Any], Awaitable[_T]],
    *,
    message: str,
    account_id: str,
    provider: str,
    budget_seconds: float,
) -> _T:
    deadline = time.monotonic() + budget_seconds
    delay = _PERSIST_BACKOFF_START_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            async with repo_factory() as repo:
                return await operation(repo)
        except Exception as exc:
            logger.error(
                "%s account_id=%s provider=%s attempt=%d error=%s",
                message,
                account_id,
                provider,
                attempt,
                type(exc).__name__,
            )
            if time.monotonic() + delay > deadline:
                raise
        await asyncio.sleep(delay)
        delay = min(delay * 2, _PERSIST_BACKOFF_MAX_SECONDS)


async def persist_exchange_result(
    repo_factory: Callable[[], AbstractAsyncContextManager[Any]],
    account_id: str,
    provider: str,
    values: Mapping[str, Any],
    expected_token: bytes,
    token_hash: str | None,
    budget_seconds: float,
) -> bool:
    return await _retry_on_fresh_repo(
        repo_factory,
        lambda repo: repo.update_tokens(
            account_id,
            **values,
            expected_refresh_token_encrypted=expected_token,
            exchange_token_hash=token_hash,
        ),
        message="Refreshed tokens not yet persisted",
        account_id=account_id,
        provider=provider,
        budget_seconds=budget_seconds,
    )


async def mark_exchange_unsaved(
    repo_factory: Callable[[], AbstractAsyncContextManager[Any]],
    account_id: str,
    provider: str,
    token_hash: str,
    expected_token: bytes,
) -> None:
    logger.error(
        "Exchange answered but tokens unsaved account_id=%s provider=%s; consumed refresh token will NOT be "
        "replayed; re-login needed if not recovered",
        account_id,
        provider,
    )
    await _retry_on_fresh_repo(
        repo_factory,
        lambda repo: repo.mark_exchange_uncertain(account_id, token_hash, expected_token, reason="unsaved"),
        message="Unsaved exchange mark not yet persisted",
        account_id=account_id,
        provider=provider,
        budget_seconds=persist_budget_seconds(),
    )
