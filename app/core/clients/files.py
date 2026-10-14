

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from collections.abc import Mapping
from typing import Any

import aiohttp

from app.core.clients.codex import CodexClient, create_codex_session, require_route_or_direct_egress_opt_in
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.types import JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute

OPENAI_FILE_UPLOAD_LIMIT_BYTES: int = 512 * 1024 * 1024


OPENAI_FILE_USE_CASE: str = "codex"


_DEFAULT_FILE_REQUEST_TIMEOUT_SECONDS: float = 60.0



_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS: float = 30.0


_FILE_FINALIZE_POLL_DELAY_SECONDS: float = 0.25




_FILES_FORWARD_HEADER_PREFIXES: tuple[str, ...] = ("x-openai-", "x-codex-")




_FILES_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_FILES_CONNECT_TIMEOUT_OVERRIDE", default=None
)
_FILES_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_FILES_TOTAL_TIMEOUT_OVERRIDE", default=None
)


def push_files_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[contextvars.Token[float | None], contextvars.Token[float | None]]:

    return (
        _FILES_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds),
        _FILES_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds),
    )


def pop_files_timeout_overrides(
    tokens: tuple[contextvars.Token[float | None], contextvars.Token[float | None]],
) -> None:
    connect_token, total_token = tokens
    _FILES_CONNECT_TIMEOUT_OVERRIDE.reset(connect_token)
    _FILES_TOTAL_TIMEOUT_OVERRIDE.reset(total_token)


def _effective_files_total_timeout(default_seconds: float = _DEFAULT_FILE_REQUEST_TIMEOUT_SECONDS) -> float:
    override = _FILES_TOTAL_TIMEOUT_OVERRIDE.get()
    if override is None:
        return default_seconds
    return max(0.001, override)


def _effective_files_connect_timeout(default_seconds: float) -> float:
    override = _FILES_CONNECT_TIMEOUT_OVERRIDE.get()
    if override is None:
        return default_seconds
    return max(0.001, override)


class FileProxyError(Exception):


    def __init__(self, status_code: int, payload: Any, *, failure_phase: str | None = None) -> None:
        super().__init__(f"upstream file request failed: status={status_code}")
        self.status_code = status_code
        self.payload = payload
        self.failure_phase = failure_phase


def _build_files_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:

    headers: dict[str, str] = {}
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    for key, value in inbound.items():
        lower = key.lower()
        if lower == "user-agent":
            headers.setdefault(key, value)
        elif lower.startswith(_FILES_FORWARD_HEADER_PREFIXES):
            headers.setdefault(key, value)
    return headers


def _parse_upstream_error_body(text: str) -> Any:

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _record_direct_route_trace(route_trace: Any | None) -> None:
    recorder = getattr(route_trace, "record_direct", None)
    if callable(recorder):
        recorder()


async def create_file(
    *,
    payload: Mapping[str, JsonValue],
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: Any | None = None,
    allow_direct_egress: bool = False,
) -> dict[str, JsonValue]:

    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/files"
    upstream_headers = _build_files_headers(headers, access_token, account_id)
    effective_total = _effective_files_total_timeout()
    effective_connect = _effective_files_connect_timeout(settings.upstream_connect_timeout_seconds)
    timeout = aiohttp.ClientTimeout(
        total=effective_total,
        sock_connect=effective_connect,
    )
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="file create",
    )
    if route is None:
        _record_direct_route_trace(route_trace)
    if route is not None:
        response = await _codex_request(
            "POST",
            url,
            route=route,
            codex_client=codex_client,
            route_trace=route_trace,
            data=body,
            headers=upstream_headers,
            timeout=effective_total,
        )
        return await _parse_file_response(response, "/files")
    try:
        async with lease_http_session(session) as client_session:
            async with client_session.post(
                url,
                data=body,
                headers=upstream_headers,
                timeout=timeout,
            ) as response:
                try:
                    text = await response.text()
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    message = str(exc) or "Request to upstream timed out"
                    raise FileProxyError(
                        502,
                        openai_error("upstream_unavailable", message),
                        failure_phase="body_read",
                    ) from exc
                if response.status >= 400:
                    raise FileProxyError(response.status, _parse_upstream_error_body(text), failure_phase="status")
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise FileProxyError(
                        502,
                        openai_error(
                            "upstream_error",
                            f"Upstream /files response was not JSON: {exc}",
                        ),
                        failure_phase="parse",
                    ) from exc
                if not isinstance(parsed, dict):
                    raise FileProxyError(
                        502,
                        openai_error(
                            "upstream_error",
                            "Upstream /files response was not a JSON object",
                        ),
                        failure_phase="parse",
                    )
                return parsed
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        message = str(exc) or "Request to upstream timed out"
        raise FileProxyError(
            502,
            openai_error("upstream_unavailable", message),
            failure_phase="connect",
        ) from exc


async def finalize_file(
    *,
    file_id: str,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: Any | None = None,
    allow_direct_egress: bool = False,
) -> dict[str, JsonValue]:

    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/files/{file_id}/uploaded"
    upstream_headers = _build_files_headers(headers, access_token, account_id)




    effective_per_poll_total = _effective_files_total_timeout()
    effective_connect = _effective_files_connect_timeout(settings.upstream_connect_timeout_seconds)
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="file finalize",
    )
    if route is None:
        _record_direct_route_trace(route_trace)
    if route is not None:
        owns_codex_client = codex_client is None
        active_codex_client = codex_client or CodexClient(create_codex_session())
        try:
            finalize_budget = min(_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS, effective_per_poll_total)
            deadline = time.monotonic() + finalize_budget
            parsed: dict[str, JsonValue] = {"status": "retry"}
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return parsed
                response = await _codex_request(
                    "POST",
                    url,
                    route=route,
                    codex_client=active_codex_client,
                    route_trace=route_trace,
                    data=b"{}",
                    headers=upstream_headers,
                    timeout=min(effective_per_poll_total, remaining),
                )
                parsed = await _parse_file_response(response, f"/files/{file_id}/uploaded")
                status = parsed.get("status")
                if status != "retry":
                    return parsed
                if time.monotonic() >= deadline:
                    return parsed
                await asyncio.sleep(_FILE_FINALIZE_POLL_DELAY_SECONDS)
                if time.monotonic() >= deadline:
                    return parsed
        except Exception as exc:
            if isinstance(exc, FileProxyError):
                raise
            message = str(exc) or "Request to upstream timed out"
            raise FileProxyError(
                502,
                openai_error("upstream_unavailable", message),
            ) from exc
        finally:
            if owns_codex_client:
                await active_codex_client.close()
    async with lease_http_session(session) as client_session:


        finalize_budget = min(_DEFAULT_FILE_FINALIZE_BUDGET_SECONDS, effective_per_poll_total)
        deadline = time.monotonic() + finalize_budget
        parsed: dict[str, JsonValue] = {"status": "retry"}
        while True:





            remaining = deadline - time.monotonic()
            if remaining <= 0:



                return parsed
            per_poll_total = min(effective_per_poll_total, remaining)
            timeout = aiohttp.ClientTimeout(
                total=per_poll_total,
                sock_connect=min(effective_connect, per_poll_total),
            )
            try:
                async with client_session.post(
                    url,
                    data=b"{}",
                    headers=upstream_headers,
                    timeout=timeout,
                ) as response:
                    try:
                        text = await response.text()
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        message = str(exc) or "Request to upstream timed out"
                        raise FileProxyError(
                            502,
                            openai_error("upstream_unavailable", message),
                            failure_phase="body_read",
                        ) from exc
                    if response.status >= 400:
                        raise FileProxyError(response.status, _parse_upstream_error_body(text), failure_phase="status")
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise FileProxyError(
                            502,
                            openai_error(
                                "upstream_error",
                                f"Upstream /files/{file_id}/uploaded response was not JSON: {exc}",
                            ),
                            failure_phase="parse",
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise FileProxyError(
                            502,
                            openai_error(
                                "upstream_error",
                                "Upstream /files/{file_id}/uploaded response was not a JSON object",
                            ),
                            failure_phase="parse",
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                message = str(exc) or "Request to upstream timed out"
                raise FileProxyError(
                    502,
                    openai_error("upstream_unavailable", message),
                    failure_phase="connect",
                ) from exc

            status = parsed.get("status")
            if status != "retry":
                return parsed
            if time.monotonic() >= deadline:




                return parsed
            await asyncio.sleep(_FILE_FINALIZE_POLL_DELAY_SECONDS)
            if time.monotonic() >= deadline:



                return parsed


async def _codex_request(
    method: str,
    url: str,
    *,
    route: ResolvedUpstreamRoute,
    codex_client: CodexClient | None,
    route_trace: Any | None = None,
    **kwargs: Any,
) -> Any:
    owns_codex_client = codex_client is None
    active_codex_client = codex_client or CodexClient(create_codex_session())
    try:
        request_with_metadata = getattr(active_codex_client, "request_with_route_metadata", None)
        if callable(request_with_metadata):
            result = await request_with_metadata(method, url, route=route, **kwargs)
            if route_trace is not None:
                route_trace.record(route=result.route, fallback_used=result.fallback_used)
            return result.response
        response = await active_codex_client.request(method, url, route=route, **kwargs)
        if route_trace is not None:
            route_trace.record(route=route, fallback_used=False)
        return response
    except Exception as exc:
        message = str(exc) or "Request to upstream timed out"
        raise FileProxyError(
            502,
            openai_error("upstream_unavailable", message),
        ) from exc
    finally:
        if owns_codex_client:
            await active_codex_client.close()


async def _parse_file_response(response: Any, endpoint: str) -> dict[str, JsonValue]:
    status_code = int(getattr(response, "status_code", getattr(response, "status", 0)))
    text_value = getattr(response, "text", None)
    if isinstance(text_value, str):
        text = text_value
    else:
        content = getattr(response, "content", b"")
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = str(content)
    if status_code >= 400:
        raise FileProxyError(status_code, _parse_upstream_error_body(text))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FileProxyError(
            502,
            openai_error(
                "upstream_error",
                f"Upstream {endpoint} response was not JSON: {exc}",
            ),
        ) from exc
    if not isinstance(parsed, dict):
        raise FileProxyError(
            502,
            openai_error(
                "upstream_error",
                f"Upstream {endpoint} response was not a JSON object",
            ),
        )
    return parsed
