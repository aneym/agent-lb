from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, AsyncContextManager
from urllib.parse import urljoin

import aiohttp

from app.core.clients import upstream_body
from app.core.config.settings import get_settings
from app.core.providers import ANTHROPIC_PROVIDER_NAME


class AnthropicTransport:
    @staticmethod
    def _encode_json_body(json_body: Mapping[str, Any]) -> bytes:
        return json.dumps(json_body, separators=(",", ":")).encode()

    def _open_messages(
        self,
        session: aiohttp.ClientSession,
        *,
        provider_name: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        raw_bytes: bytes,
        openai_chat: bool = False,
    ) -> AsyncContextManager[aiohttp.ClientResponse]:
        if type(self)._open_upstream_response is AnthropicTransport._open_upstream_response:
            return self._open_upstream_response(
                session,
                provider_name=provider_name,
                headers=headers,
                json_body=json_body,
                openai_chat=openai_chat,
                raw_bytes=raw_bytes,
            )
        if openai_chat:
            return self._open_upstream_response(
                session,
                provider_name=provider_name,
                headers=headers,
                json_body=json_body,
                openai_chat=True,
            )
        return self._open_upstream_response(
            session,
            provider_name=provider_name,
            headers=headers,
            json_body=json_body,
        )

    def _open_upstream_response(
        self,
        session: aiohttp.ClientSession,
        *,
        provider_name: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
        openai_chat: bool = False,
        raw_bytes: bytes | None = None,
    ) -> AsyncContextManager[aiohttp.ClientResponse]:
        from app.modules.proxy.anthropic_service import _upstream_base_url

        settings = get_settings()
        url = urljoin(
            _upstream_base_url(provider_name).rstrip("/") + "/", "v1/chat/completions" if openai_chat else "v1/messages"
        )
        timeout = aiohttp.ClientTimeout(
            total=settings.proxy_request_budget_seconds,
            connect=settings.upstream_connect_timeout_seconds,
        )
        if provider_name == ANTHROPIC_PROVIDER_NAME:
            return upstream_body.post_json(
                session, url, json_body=json_body, headers=headers, timeout=timeout, raw_bytes=raw_bytes
            )
        if raw_bytes is not None:
            return session.post(url, data=raw_bytes, headers=upstream_body.json_headers(headers), timeout=timeout)
        return session.post(url, json=json_body, headers=headers, timeout=timeout)

    def _open_count_tokens_response(
        self,
        session: aiohttp.ClientSession,
        *,
        provider_name: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ) -> AsyncContextManager[aiohttp.ClientResponse]:
        from app.modules.proxy.anthropic_service import _upstream_base_url

        settings = get_settings()
        url = urljoin(_upstream_base_url(provider_name).rstrip("/") + "/", "v1/messages/count_tokens")
        timeout = aiohttp.ClientTimeout(
            total=30.0,
            connect=settings.upstream_connect_timeout_seconds,
        )
        if provider_name == ANTHROPIC_PROVIDER_NAME:
            return upstream_body.post_json(session, url, json_body=json_body, headers=headers, timeout=timeout)
        return session.post(url, json=json_body, headers=headers, timeout=timeout)
