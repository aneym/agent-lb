from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Mapping
from datetime import datetime, timezone

from app.core.anthropic.models import AnthropicMessageRequest
from app.core.config.settings import get_settings
from app.core.utils.client_session import CHILD_THREAD_PREFIX, get_caller_seat, get_client_session_id, get_thread_id
from app.db.models import AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError
from app.modules.proxy.load_balancer import selectable_accounts
from app.modules.usage.repository import UsageRepository

logger = logging.getLogger(__name__)

_ROOM_TTL_SECONDS = 15.0
_SEEN_LIMIT = 8192
_FALLBACK_WAIT_SECONDS = 300
_seen: OrderedDict[str, None] = OrderedDict()
_room_cache: tuple[float, float, int | None] | None = None


def background_start(headers: Mapping[str, str], payload: AnthropicMessageRequest) -> bool:
    if "x-claude-code-agent-id" in headers or len(payload.messages) != 1:
        return False
    priority = (headers.get("x-agent-lb-priority") or "").strip().lower()
    if priority == "high":
        return False
    seats = {seat.strip() for seat in get_settings().anthropic_start_gate_background_seats.split(",") if seat.strip()}
    return (
        priority == "background"
        or (get_thread_id() or "").startswith(CHILD_THREAD_PREFIX)
        or (get_caller_seat() or "") in seats
    )


async def held_start(headers: Mapping[str, str], payload: AnthropicMessageRequest) -> AnthropicProxyError | None:
    settings = get_settings()
    session = get_client_session_id()
    if not settings.anthropic_start_gate_enabled or not payload.model.startswith("claude-"):
        return None
    if session is not None and session in _seen:
        _seen.move_to_end(session)
        return None
    if not background_start(headers, payload):
        _remember(session)
        return None
    room, retry_at = await pool_room()
    if room >= settings.anthropic_start_gate_min_room:
        _remember(session)
        return None
    now = int(time.time())
    retry_at = retry_at if retry_at is not None and retry_at > now else now + _FALLBACK_WAIT_SECONDS
    until = datetime.fromtimestamp(retry_at, tz=timezone.utc).strftime("%H:%MZ")
    logger.info(
        "start_gate_held session=%s thread=%s seat=%s room=%.2f retry_at=%s",
        (session or "-")[:80],
        (get_thread_id() or "-")[:120],
        get_caller_seat() or "-",
        room,
        retry_at,
    )
    return AnthropicProxyError(
        429,
        f"agent-lb: the pool's 5-hour room is low ({room:.2f} of one account left). "
        f"New background sessions wait until {until}; chats and offices keep running. Retry after {until}.",
        code="start_gate_held",
        retry_at=retry_at,
    )


async def pool_room() -> tuple[float, int | None]:
    global _room_cache
    now = time.time()
    if _room_cache is not None and now - _room_cache[0] < _ROOM_TTL_SECONDS:
        return _room_cache[1], _room_cache[2]
    async with get_background_session() as session:
        accounts = [
            account
            for account in selectable_accounts(await AccountsRepository(session).list_accounts())
            if account.provider == "anthropic" and account.status == AccountStatus.ACTIVE
        ]
        ids = [account.id for account in accounts]
        usage = UsageRepository(session)
        primary = await usage.latest_by_account(window="primary", account_ids=ids)
        weekly = await usage.latest_by_account(window="secondary", account_ids=ids)
    room = 0.0
    resets: list[int] = []
    for account_id in ids:
        week = weekly.get(account_id)
        if week is not None and _spent(week.used_percent, week.reset_at, now):
            continue
        entry = primary.get(account_id)
        if entry is None or entry.used_percent is None:
            room += 1.0
            continue
        if entry.reset_at is not None and entry.reset_at > now:
            resets.append(int(entry.reset_at))
            room += max(0.0, 100.0 - float(entry.used_percent)) / 100.0
        else:
            room += 1.0
    _room_cache = (now, room, min(resets) if resets else None)
    return room, _room_cache[2]


def _spent(used_percent: float | None, reset_at: int | None, now: float) -> bool:
    return used_percent is not None and float(used_percent) >= 100.0 and (reset_at is None or reset_at > now)


def _remember(session: str | None) -> None:
    if session is None:
        return
    _seen[session] = None
    while len(_seen) > _SEEN_LIMIT:
        _seen.popitem(last=False)
