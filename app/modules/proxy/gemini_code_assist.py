from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import aiohttp

from app.core.clients.code_assist import (
    CODE_ASSIST_USER_AGENT,
    CodeAssistError,
    read_error_body,
    resolve_code_assist_project,
)
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.providers.gemini_models import GEMINI_HYDROGEN_MODEL, GEMINI_NEON_MODEL
from app.core.utils.request_id import get_request_id
from app.modules.accounts.gemini_project import remember_gemini_project
from app.modules.proxy.gemini_code_assist_errors import upstream_error
from app.modules.proxy.gemini_code_assist_wire import code_assist_request, gemini_chunk
from app.modules.proxy.gemini_prefix_fingerprint import fingerprint_id, prefix_fingerprint
from app.modules.proxy.gemini_request import gemini_session_id
from app.modules.proxy.gemini_upstream import GeminiUpstreamCall, GeminiUpstreamError

logger = logging.getLogger(__name__)

STREAM_PATH = "/v1internal:streamGenerateContent"
CONNECT_TIMEOUT_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 300.0


async def _project(call: GeminiUpstreamCall) -> str:
    if call.project_id:
        return call.project_id
    try:
        project_id = await resolve_code_assist_project(call.access_token)
    except CodeAssistError as exc:
        raise upstream_error(exc.status_code, exc.body) from exc
    await remember_gemini_project(call.account_id, project_id)
    return project_id


async def _lines(content: aiohttp.StreamReader) -> AsyncIterator[str]:
    pending = b""
    async for data in content.iter_any():
        pending += data
        *complete, pending = pending.split(b"\n")
        for raw in complete:
            yield raw.decode("utf-8", errors="replace").rstrip("\r")
    if pending:
        yield pending.decode("utf-8", errors="replace").rstrip("\r")


async def _sse_payloads(content: aiohttp.StreamReader) -> AsyncIterator[object]:
    lines: list[str] = []
    async for line in _lines(content):
        if line.startswith("data:"):
            lines.append(line[5:].strip())
        elif line == "" and lines:
            text, lines = "\n".join(lines), []
            yield _parsed(text)
    if lines:
        yield _parsed("\n".join(lines))


def _parsed(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise GeminiUpstreamError(502, "Gemini Code Assist sent a malformed stream chunk.", kind="transient") from exc


def _raise_stream_error(item: object) -> None:
    error = item.get("error") if isinstance(item, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        status = code if isinstance(code, int) and 400 <= code < 600 else 502
        raise upstream_error(status, json.dumps(item).encode())


def resolve_upstream_model(call_model: str, effort: str, default_neon_model: str) -> str:
    if call_model == GEMINI_NEON_MODEL:
        return f"{default_neon_model}-{effort}"
    if call_model == GEMINI_HYDROGEN_MODEL:
        return f"{GEMINI_HYDROGEN_MODEL}-{effort}"
    return call_model


class CodeAssistGeminiUpstream:
    def stream(self, call: GeminiUpstreamCall) -> AsyncGenerator[dict[str, Any], None]:
        return self._stream(call)

    async def _stream(self, call: GeminiUpstreamCall) -> AsyncGenerator[dict[str, Any], None]:
        settings = get_settings()
        project_id = await _project(call)
        model = resolve_upstream_model(call.model, call.effort, settings.gemini_code_assist_model)
        payload = code_assist_request(
            call.body,
            model=model,
            project=project_id,
            user_prompt_id=uuid.uuid4().hex,
        )
        if call.model == GEMINI_NEON_MODEL and logger.isEnabledFor(logging.INFO):
            logger.info(
                "gemini_prefix request_hash=%s session_hash=%s fp=%s",
                fingerprint_id(get_request_id()),
                fingerprint_id(gemini_session_id(call.body)),
                json.dumps(prefix_fingerprint(payload), separators=(",", ":")),
            )
        url = f"{settings.gemini_code_assist_base_url.rstrip('/')}{STREAM_PATH}"
        headers = {
            "Authorization": f"Bearer {call.access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": CODE_ASSIST_USER_AGENT,
        }
        timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=CONNECT_TIMEOUT_SECONDS, sock_read=READ_TIMEOUT_SECONDS
        )
        try:
            async with lease_http_session() as session:
                async with session.post(
                    url, params={"alt": "sse"}, json=payload, headers=headers, timeout=timeout, allow_redirects=False
                ) as resp:
                    if resp.status >= 300:
                        raw = await read_error_body(resp.content)
                        logger.warning("gemini_code_assist_failed path=%s status=%s", STREAM_PATH, resp.status)
                        raise upstream_error(resp.status, raw)
                    answered = False
                    async for item in _sse_payloads(resp.content):
                        _raise_stream_error(item)
                        chunk = gemini_chunk(item)
                        if chunk is not None:
                            answered = True
                            yield chunk
                    if not answered:
                        raise GeminiUpstreamError(502, "Gemini Code Assist sent no response.", kind="transient")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("gemini_code_assist_unreachable path=%s error=%s", STREAM_PATH, exc.__class__.__name__)
            raise GeminiUpstreamError(502, "Gemini Code Assist is unreachable.", kind="transient") from exc
