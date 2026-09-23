"""Account-scoped Claude reset grants. Never retry a redemption implicitly."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urljoin
from uuid import UUID

import aiohttp
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from app.core.clients.http import lease_http_session
from app.core.clients.rate_limit_resets import ResetCreditsError
from app.core.config.settings import get_settings
from app.core.types import JsonObject


class ResetGrant(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    label: str = ""
    resets_left: int = Field(ge=0)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    clears: list[str] = Field(default_factory=list)
    paused: bool = False
    usable_now: bool = False
    use_requires_limit: bool = True


class ResetStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    eligible: bool
    ineligible_reason: str | None = None
    at_limit: bool = False
    exhausted: list[str] = Field(default_factory=list)
    grants: list[ResetGrant] = Field(default_factory=list)
    next_grant_id: str | None = None
    cooldown_until: AwareDatetime | None = None

    def redemption_blocker(self, now: datetime, credit_id: str | None = None) -> str | None:
        if not self.eligible:
            return "provider_ineligible"
        if self.cooldown_until is not None and _utc(self.cooldown_until) > now:
            return "provider_cooldown"
        grant_id = credit_id or self.next_grant_id
        if grant_id != self.next_grant_id or not grant_id:
            return "no_selected_grant"
        for grant in self.grants:
            if grant.id != grant_id:
                continue
            if grant.use_requires_limit and (not self.at_limit or not self.exhausted):
                return "not_at_limit"
            if not grant.usable_now or grant.paused or grant.resets_left < 1:
                return "grant_unavailable"
            if grant.starts_at is not None and _utc(grant.starts_at) > now:
                return "grant_not_started"
            if grant.ends_at is not None and _utc(grant.ends_at) <= now:
                return "grant_expired"
            if grant.use_requires_limit and not set(self.exhausted).issubset(grant.clears):
                return "grant_cannot_clear_limit"
            return None
        return "no_selected_grant"

    def usable_grant(self, now: datetime, credit_id: str | None = None) -> ResetGrant | None:
        # Early use is permitted only when this specific grant says a limit is
        # not required; ordinary grants still need recoverable exhaustion.
        if self.redemption_blocker(now, credit_id) is not None:
            return None
        grant_id = credit_id or self.next_grant_id
        for grant in self.grants:
            if grant.id == grant_id:
                return grant
        return None


class ResetResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    result: Literal["reset", "already_used", "not_limited", "cooldown", "ineligible", "unavailable"]
    reason: str | None = None
    resets_left: int | None = Field(default=None, ge=0)
    cleared: list[str] = Field(default_factory=list)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ResetCreditsError(502, "Reset grant contains a timezone-less timestamp")
    return value.astimezone(timezone.utc)


async def fetch_status(*, access_token: str) -> ResetStatus:
    data = await _request("GET", "api/oauth/usage?cedar_ember=1&skip_spend=1", access_token)
    try:
        return ResetStatus.model_validate(data.get("cedar_ember"))
    except ValidationError as exc:
        raise ResetCreditsError(502, "Claude reset status is unavailable or malformed") from exc


async def redeem(*, access_token: str, grant_id: str, request_id: str) -> ResetResult:
    # Fetch the organization using the same account token: local account IDs
    # identify users and cannot safely be substituted for organization UUIDs.
    profile = await _request("GET", "api/oauth/profile", access_token)
    organization = profile.get("organization")
    org_id = organization.get("uuid") if isinstance(organization, dict) else None
    try:
        org_uuid = str(UUID(str(org_id)))
        UUID(request_id)
    except ValueError as exc:
        raise ResetCreditsError(502, "Claude reset organization or request ID is invalid") from exc
    ResetGrant(id=grant_id, resets_left=1)
    data = await _request(
        "POST",
        f"api/organizations/{org_uuid}/reset_rate_limits",
        access_token,
        body={"program": "cedar_ember", "grant_id": grant_id, "request_id": request_id},
    )
    try:
        return ResetResult.model_validate(data)
    except ValidationError as exc:
        raise ResetCreditsError(502, "Claude reset outcome is unknown; reconciliation required") from exc


async def _request(method: str, path: str, access_token: str, *, body: JsonObject | None = None) -> JsonObject:
    url = urljoin(get_settings().anthropic_upstream_base_url.rstrip("/") + "/", path)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-cli/2.1.280 (external, cli)",
        "Accept": "application/json",
    }
    try:
        async with lease_http_session() as session:
            async with session.request(
                method,
                url,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=25, sock_connect=10),
            ) as response:
                if response.status >= 400:
                    raise ResetCreditsError(response.status, f"Claude reset request failed ({response.status})")
                data = await response.json(content_type=None)
                if not isinstance(data, dict):
                    raise ResetCreditsError(502, "Malformed Claude reset response")
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise ResetCreditsError(502, "Claude reset request failed; redemption outcome may be unknown") from exc
