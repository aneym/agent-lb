from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TypeAlias

from app.core.auth.refresh import (
    RefreshError,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account
from app.modules.accounts.auth_tokens import _refresh_token_material_fingerprint

logger = logging.getLogger(__name__)


_RefreshSingleflightKey: TypeAlias = tuple[str, str]


class _RefreshSingleflight:
    def __init__(self) -> None:
        self._inflight: dict[_RefreshSingleflightKey, asyncio.Task[Account]] = {}
        self._recent_failures: dict[_RefreshSingleflightKey, tuple[float, tuple[str, str, bool]]] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        key: _RefreshSingleflightKey,
        factory: Callable[[], Coroutine[object, object, Account]],
    ) -> Account:
        account_id = key[0]
        async with self._lock:
            self._purge_stale_versions(account_id, keep_key=key)
            cached_failure = self._recent_failures.get(key)
            if cached_failure is not None:
                expires_at, failure = cached_failure
                if expires_at > time.monotonic():
                    code, message, is_permanent = failure
                    raise RefreshError(code, message, is_permanent)
                self._recent_failures.pop(key, None)
            task = self._inflight.get(key)
            if task is not None and task.done() and not task.cancelled() and task.exception() is None:
                pass
            elif task is None or task.done():
                task = asyncio.create_task(factory())
                self._inflight[key] = task
                task.add_done_callback(lambda done, *, cache_key=key: self._schedule_complete(cache_key, done))
        assert task is not None
        return await asyncio.shield(task)

    def _schedule_complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        asyncio.create_task(self._complete(key, task))

    async def _complete(self, key: _RefreshSingleflightKey, task: asyncio.Task[Account]) -> None:
        try:
            async with self._lock:
                current = self._inflight.get(key)
                if current is task:
                    self._inflight.pop(key, None)
                if task.cancelled():
                    self._recent_failures.pop(key, None)
                    return
                try:
                    task.result()
                except RefreshError as exc:
                    ttl = max(0.0, float(get_settings().proxy_refresh_failure_cooldown_seconds))
                    if ttl > 0 and not exc.transport_error:
                        self._recent_failures[key] = (
                            time.monotonic() + ttl,
                            (exc.code, exc.message, exc.is_permanent),
                        )
                    else:
                        self._recent_failures.pop(key, None)
                except BaseException:
                    self._recent_failures.pop(key, None)
                else:
                    self._recent_failures.pop(key, None)
        except BaseException:
            logger.exception("Refresh singleflight completion cleanup failed key=%s", key)

    def _purge_stale_versions(self, account_id: str, *, keep_key: _RefreshSingleflightKey) -> None:
        stale_failures = [key for key in self._recent_failures if key[0] == account_id and key != keep_key]
        for key in stale_failures:
            self._recent_failures.pop(key, None)
        stale_inflight = [
            key for key, task in self._inflight.items() if key[0] == account_id and key != keep_key and task.done()
        ]
        for key in stale_inflight:
            self._inflight.pop(key, None)

    def clear(self) -> None:
        self._inflight.clear()
        self._recent_failures.clear()


_REFRESH_SINGLEFLIGHT = _RefreshSingleflight()


def _refresh_singleflight_key(encryptor: TokenEncryptor, account: Account) -> _RefreshSingleflightKey:
    return (account.id, _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted))


def _clear_refresh_singleflight_state() -> None:
    _REFRESH_SINGLEFLIGHT.clear()
