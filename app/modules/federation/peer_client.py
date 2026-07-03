from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from app.modules.federation.exceptions import FederationPeerRequestError
from app.modules.federation.schemas import (
    FederationAuthPayload,
    FederationCheckoutResponse,
    FederationMirrorResponse,
    FederationTransferStatusResponse,
)


@dataclass(frozen=True, slots=True)
class CheckoutPeerResult:
    nonce: str
    owner_instance_id: str
    auth: FederationAuthPayload


@dataclass(frozen=True, slots=True)
class CheckinPeerResult:
    settled: bool


class FederationPeerClient(Protocol):
    async def fetch_mirror(self, *, peer_url: str, token: str) -> FederationMirrorResponse: ...

    async def checkout(
        self, *, peer_url: str, token: str, account_id: str, taker_instance_id: str
    ) -> CheckoutPeerResult: ...

    async def checkout_confirm(self, *, peer_url: str, token: str, nonce: str) -> None: ...

    async def checkin(
        self,
        *,
        peer_url: str,
        token: str,
        account_id: str,
        nonce: str,
        auth: FederationAuthPayload,
    ) -> CheckinPeerResult: ...


class AiohttpFederationPeerClient:
    """Default runtime FederationPeerClient. Bounded timeouts, no hidden retries."""

    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self._timeout_seconds = timeout_seconds

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self._timeout_seconds)

    async def fetch_mirror(self, *, peer_url: str, token: str) -> FederationMirrorResponse:
        async with aiohttp.ClientSession(timeout=self._timeout(), trust_env=False) as session:
            async with session.get(f"{peer_url}/api/federation/mirror", headers=_bearer_headers(token)) as response:
                data = await _json_or_raise(response)
        return FederationMirrorResponse.model_validate(data)

    async def checkout(
        self, *, peer_url: str, token: str, account_id: str, taker_instance_id: str
    ) -> CheckoutPeerResult:
        async with aiohttp.ClientSession(timeout=self._timeout(), trust_env=False) as session:
            async with session.post(
                f"{peer_url}/api/federation/checkout",
                json={"account_id": account_id, "taker_instance_id": taker_instance_id},
                headers=_bearer_headers(token),
            ) as response:
                data = await _json_or_raise(response)
        parsed = FederationCheckoutResponse.model_validate(data)
        return CheckoutPeerResult(nonce=parsed.nonce, owner_instance_id=parsed.owner_instance_id, auth=parsed.auth)

    async def checkout_confirm(self, *, peer_url: str, token: str, nonce: str) -> None:
        async with aiohttp.ClientSession(timeout=self._timeout(), trust_env=False) as session:
            async with session.post(
                f"{peer_url}/api/federation/checkout/confirm",
                json={"nonce": nonce},
                headers=_bearer_headers(token),
            ) as response:
                await _json_or_raise(response)

    async def checkin(
        self,
        *,
        peer_url: str,
        token: str,
        account_id: str,
        nonce: str,
        auth: FederationAuthPayload,
    ) -> CheckinPeerResult:
        async with aiohttp.ClientSession(timeout=self._timeout(), trust_env=False) as session:
            async with session.post(
                f"{peer_url}/api/federation/checkin",
                json={"account_id": account_id, "nonce": nonce, "auth": auth.model_dump(mode="json")},
                headers=_bearer_headers(token),
            ) as response:
                data = await _json_or_raise(response)
        parsed = FederationTransferStatusResponse.model_validate(data)
        return CheckinPeerResult(settled=parsed.state == "settled")


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _json_or_raise(response: aiohttp.ClientResponse) -> Any:
    if response.status >= 400:
        text = await response.text()
        raise FederationPeerRequestError(
            f"Federation peer request failed status={response.status} body={text[:200]!r}",
            status_code=response.status,
        )
    return await response.json()
