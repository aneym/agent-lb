from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from app import solo_headroom

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class PostTarget:
    url: str
    token: str | None
    interval_seconds: int


def _token() -> str | None:
    path = os.environ.get("AGENT_LB_SOLO_HEADROOM_TOKEN_FILE")
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8").strip() or None
    return os.environ.get("AGENT_LB_SOLO_HEADROOM_TOKEN") or None


def target_from_env() -> PostTarget | None:
    url = (os.environ.get("AGENT_LB_SOLO_HEADROOM_URL") or "").strip()
    if not url:
        return None
    interval = int(os.environ.get("AGENT_LB_SOLO_HEADROOM_INTERVAL_SECONDS") or DEFAULT_INTERVAL_SECONDS)
    return PostTarget(url=url, token=_token(), interval_seconds=max(30, interval))


async def summary() -> dict[str, Any]:
    from app.core.config.settings import get_settings
    from app.db.session import get_background_session

    async with get_background_session() as session:
        return await solo_headroom.build(session, get_settings().local_instance_id)


async def post_once(target: PostTarget, body: dict[str, Any]) -> int:
    headers = {"Content-Type": "application/json"}
    if target.token:
        headers["Authorization"] = f"Bearer {target.token}"
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async with client.post(target.url, json=body, headers=headers) as response:
            return response.status


async def run_forever(target: PostTarget) -> None:
    while True:
        try:
            status = await post_once(target, await summary())
            logger.info("solo headroom posted", extra={"status": status})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("solo headroom post failed", exc_info=True)
        await asyncio.sleep(target.interval_seconds)
