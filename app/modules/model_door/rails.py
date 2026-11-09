from __future__ import annotations

import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

CACHE_LIMIT = 10_000


@dataclass(frozen=True)
class BoxPass:
    owner: str
    agent: str
    box: str
    expires_at: str


class RailsUnavailable(Exception):
    pass


class Rails:
    def __init__(self, base: str, relay_key: str, pass_ttl_s: float) -> None:
        self._client = httpx.AsyncClient(base_url=base, headers={"authorization": f"Bearer {relay_key}"}, timeout=10.0)
        self._ttl = pass_ttl_s
        self._passes: dict[str, tuple[float, BoxPass | None]] = {}

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            reply = await self._client.post(path, json=body)
        except httpx.HTTPError as error:
            raise RailsUnavailable(f"{path}: {type(error).__name__}") from error
        if reply.status_code >= 500 or reply.status_code == 401:
            raise RailsUnavailable(f"{path}: HTTP {reply.status_code}")
        return reply.json()

    async def box_pass(self, given: str) -> BoxPass | None:
        digest = sha256(given.encode()).hexdigest()
        now = time.monotonic()
        held = self._passes.get(digest)
        if held and held[0] > now:
            found = held[1]
            return found if found is None or found.expires_at > _utc_now() else None
        answer = await self._post("/api/boxes/pass/check", {"pass": given})
        found = (
            BoxPass(answer["owner"], answer["agent"], answer["box"], answer["expires_at"])
            if answer.get("live") is True
            else None
        )
        if len(self._passes) >= CACHE_LIMIT:
            self._passes = {k: v for k, v in self._passes.items() if v[0] > now}
        self._passes[digest] = (now + self._ttl, found)
        return found

    async def model_check(self, box_pass: BoxPass) -> dict[str, Any]:
        return await self._post("/api/spend/model/check", {"owner": box_pass.owner, "agent": box_pass.agent})

    async def report_usage(self, box_pass: BoxPass, report: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/api/spend/model/usage", {"owner": box_pass.owner, "agent": box_pass.agent, **report})

    async def close(self) -> None:
        await self._client.aclose()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
