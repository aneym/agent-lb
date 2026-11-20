from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

import aiohttp
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, ValidationError

from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)

CODE_ASSIST_USER_AGENT = "antigravity/1.0.0 agent-lb"
CODE_ASSIST_METADATA = {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
ONBOARD_POLL_SECONDS = 5.0
ONBOARD_MAX_POLLS = 12
FREE_TIER_ID = "free-tier"
ERROR_BODY_LIMIT = 64 * 1024


class CodeAssistError(Exception):
    def __init__(self, status_code: int, message: str, *, body: bytes = b"") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.body = body


async def read_error_body(content: aiohttp.StreamReader) -> bytes:
    raw = b""
    try:
        while len(raw) < ERROR_BODY_LIMIT:
            data = await content.read(ERROR_BODY_LIMIT - len(raw))
            if not data:
                break
            raw += data
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return raw
    return raw


class _Tier(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictStr | None = None
    isDefault: StrictBool | None = None


class _LoadCodeAssistAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cloudaicompanionProject: StrictStr | None = None
    currentTier: _Tier | None = None
    allowedTiers: list[_Tier] = []


class _Project(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictStr | None = None


class _OnboardResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cloudaicompanionProject: _Project | None = None


class _OnboardAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: StrictStr | None = None
    done: StrictBool | None = None
    response: _OnboardResponse | None = None


async def resolve_code_assist_project(access_token: str, *, session: aiohttp.ClientSession | None = None) -> str:
    base_url = get_settings().gemini_code_assist_base_url.rstrip("/")
    async with lease_http_session(session) as client_session:
        loaded = _validated(
            _LoadCodeAssistAnswer,
            await _call(
                client_session,
                f"{base_url}/v1internal:loadCodeAssist",
                access_token,
                {"metadata": CODE_ASSIST_METADATA},
            ),
        )
        if loaded.cloudaicompanionProject:
            return loaded.cloudaicompanionProject
        tier_id = (loaded.currentTier.id if loaded.currentTier else None) or next(
            (tier.id for tier in loaded.allowedTiers if tier.isDefault and tier.id), FREE_TIER_ID
        )
        body = {"tierId": tier_id, "metadata": CODE_ASSIST_METADATA}
        answer = _validated(
            _OnboardAnswer, await _call(client_session, f"{base_url}/v1internal:onboardUser", access_token, body)
        )
        operation_name = answer.name
        for poll in range(ONBOARD_MAX_POLLS + 1):
            if answer.done:
                project = answer.response.cloudaicompanionProject if answer.response else None
                if project is not None and project.id:
                    return project.id
                break
            if poll == ONBOARD_MAX_POLLS:
                break
            await asyncio.sleep(ONBOARD_POLL_SECONDS)
            operation_name = answer.name or operation_name
            answer = _validated(
                _OnboardAnswer,
                await _call(client_session, f"{base_url}/v1internal/{operation_name}", access_token)
                if operation_name
                else await _call(client_session, f"{base_url}/v1internal:onboardUser", access_token, body),
            )
    raise CodeAssistError(504, "Code Assist onboarding did not finish")


async def post_code_assist(
    path: str,
    access_token: str,
    body: Mapping[str, object],
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    base_url = get_settings().gemini_code_assist_base_url.rstrip("/")
    async with lease_http_session() as client_session:
        return await _call(client_session, f"{base_url}{path}", access_token, body, timeout_seconds=timeout_seconds)


def _validated[Answer: BaseModel](model: type[Answer], data: dict[str, object]) -> Answer:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise CodeAssistError(502, f"Code Assist answer invalid ({exc.error_count()} errors)") from exc


async def _call(
    client_session: aiohttp.ClientSession,
    url: str,
    access_token: str,
    body: Mapping[str, object] | None = None,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": CODE_ASSIST_USER_AGENT,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with client_session.request(
            "GET" if body is None else "POST", url, json=body, headers=headers, timeout=timeout, allow_redirects=False
        ) as resp:
            if resp.status >= 300:
                logger.warning("Code Assist request failed path=%s status=%s", url.rsplit("/", 1)[-1], resp.status)
                raise CodeAssistError(
                    resp.status, f"Code Assist answered {resp.status}", body=await read_error_body(resp.content)
                )
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise CodeAssistError(502, f"Code Assist unreachable: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict):
        raise CodeAssistError(502, "Code Assist answer was not an object")
    return data
