from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator

from app.core.anthropic.models import AnthropicMessageRequest
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger(__name__)

THREAD_NOT_FOUND_MESSAGE = (
    "No thread state was found for the requested `previous_message_id`. "
    'Replay the full conversation with `thread: {"type": "create"}` to start a new Thread.'
)
_KEY_PREFIX = "anthropic-thread:"
_MESSAGE_ID = re.compile(rb'"id"\s*:\s*"(msg_[A-Za-z0-9_-]{1,200})"')
_SCAN_LIMIT = 65536
_INFLIGHT_WAIT_SECONDS = 600.0
_UNSERVABLE = frozenset(
    {
        AccountStatus.PAUSED,
        AccountStatus.QUOTA_EXCEEDED,
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.DEACTIVATED,
    }
)
_inflight: dict[str, tuple[str, asyncio.Event]] = {}


def thread_not_found() -> AnthropicProxyError:
    return AnthropicProxyError(
        404,
        THREAD_NOT_FOUND_MESSAGE,
        code="not_found_error",
        details={"error_code": "thread_not_found"},
    )


class ThreadAnchor:
    def __init__(self, payload: AnthropicMessageRequest, repo_factory: ProxyRepoFactory, *, enabled: bool) -> None:
        thread = (payload.model_extra or {}).get("thread") if enabled else None
        self._threaded = isinstance(thread, dict)
        previous = thread.get("previous_message_id") if isinstance(thread, dict) else None
        continues = isinstance(thread, dict) and thread.get("type") == "continue"
        self._previous = previous if continues and isinstance(previous, str) and previous else None
        self._repo_factory = repo_factory
        self._pinned: Account | None = None
        self._serving: str | None = None

    async def pinned(self) -> Account | None:
        if self._previous is None:
            return None
        account_id, state = await self._anchor_account_id(self._previous)
        if account_id is None:
            logger.info("anthropic_thread_anchor previous=%s state=%s", self._previous[-12:], state)
            return None
        async with self._repo_factory() as repos:
            account = await repos.accounts.get_by_id(account_id)
            if account is not None:
                repos.accounts.session.expunge(account)
        servable = account is not None and account.status not in _UNSERVABLE
        logger.info(
            "anthropic_thread_anchor previous=%s state=%s account=%s servable=%s",
            self._previous[-12:],
            state,
            account_id[:8],
            servable,
        )
        if account is None or not servable:
            raise thread_not_found()
        self._pinned = account
        return account

    def no_failover(self) -> Account | None:
        if self._pinned is not None:
            logger.info("anthropic_thread_anchor previous=%s state=no_failover", (self._previous or "")[-12:])
            raise thread_not_found()
        return None

    def serving(self, account: Account) -> Account:
        self._serving = account.id
        return account

    def watch(self, body: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        return self._watched(body) if self._threaded else body

    async def _watched(self, body: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        scanned = bytearray()
        message_id: str | None = None
        account_id: str | None = None
        try:
            async for chunk in body:
                if message_id is None and self._serving is not None and len(scanned) < _SCAN_LIMIT:
                    scanned.extend(chunk)
                    match = _MESSAGE_ID.search(scanned)
                    if match is not None:
                        message_id = match.group(1).decode()
                        account_id = self._serving
                        _inflight[message_id] = (account_id, asyncio.Event())
                yield chunk
            if message_id is not None and account_id is not None:
                await self._remember(message_id, account_id)
        finally:
            entry = _inflight.pop(message_id, None) if message_id is not None else None
            if entry is not None:
                entry[1].set()

    async def _anchor_account_id(self, message_id: str) -> tuple[str | None, str]:
        entry = _inflight.get(message_id)
        if entry is not None:
            try:
                await asyncio.wait_for(entry[1].wait(), _INFLIGHT_WAIT_SECONDS)
            except TimeoutError:
                return entry[0], "inflight_timeout"
            return entry[0], "inflight_waited"
        async with self._repo_factory() as repos:
            row = await repos.sticky_sessions.get_entry(_KEY_PREFIX + message_id, kind=StickySessionKind.CODEX_SESSION)
            account_id = row.account_id if row is not None else None
        return (account_id, "stored") if account_id is not None else (None, "unknown")

    async def _remember(self, message_id: str, account_id: str) -> None:
        try:
            async with self._repo_factory() as repos:
                await repos.sticky_sessions.upsert(
                    _KEY_PREFIX + message_id, account_id, kind=StickySessionKind.CODEX_SESSION
                )
        except Exception:
            logger.warning("anthropic_thread_anchor_store_failed message=%s", message_id[-12:], exc_info=True)
