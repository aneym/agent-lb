from __future__ import annotations

import asyncio
import logging

import aiohttp

from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)


async def nudge_federation_mirror() -> bool:
    settings = get_settings()
    token = settings.effective_federation_mirror_token
    origins = {
        origin.strip().rstrip("/")
        for origin in (settings.federation_mirror_nudge_url or "").split(",")
        if origin.strip()
    }
    if not token or not origins:
        return False
    timeout = aiohttp.ClientTimeout(total=1)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        results = await asyncio.gather(*(_nudge(session, origin, token) for origin in origins))
    return all(results)


async def _nudge(session: aiohttp.ClientSession, origin: str, token: str) -> bool:
    try:
        async with session.post(
            f"{origin}/api/federation/mirror/nudge",
            headers={"Authorization": f"Bearer {token}"},
            allow_redirects=False,
        ) as response:
            if response.status == 202:
                return True
            logger.warning("Federation mirror nudge rejected (HTTP %d)", response.status)
    except (aiohttp.ClientError, TimeoutError):
        logger.warning("Federation mirror nudge delivery failed")
    return False
