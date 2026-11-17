from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.settings import get_settings
from app.core.middleware.api_firewall import is_trusted_proxy_source, parse_trusted_proxy_networks
from app.core.request_locality import INGRESS_PROOF_HEADER, INGRESS_PROOF_SCOPE_KEY


class DashboardAuthProxyHeaderSanitizerMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        settings = get_settings()
        self._enabled = settings.dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER
        self._trust_proxy_headers = settings.firewall_trust_proxy_headers
        self._trusted_proxy_networks = parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)
        self._trusted_header_name = settings.dashboard_auth_proxy_header.lower().encode("latin-1")
        self._ingress_proof = _read_proof(settings.dashboard_keyless_proxy_proof_file)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = self._take_ingress_proof(scope)
        if not self._enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = cast(tuple[str, int] | None, scope.get("client"))
        client_host = client[0] if client is not None else None
        if (
            self._trust_proxy_headers
            and client_host
            and is_trusted_proxy_source(client_host, self._trusted_proxy_networks)
        ):
            await self.app(scope, receive, send)
            return

        headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
        if not any(name.lower() == self._trusted_header_name for name, _ in headers):
            await self.app(scope, receive, send)
            return

        scrubbed_scope = {**scope, "headers": _filter_headers(headers, self._trusted_header_name)}
        await self.app(scrubbed_scope, receive, send)

    def _take_ingress_proof(self, scope: Scope) -> Scope:
        headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
        proofs = [value for name, value in headers if name.lower() == INGRESS_PROOF_HEADER]
        if not proofs:
            return scope
        verified = (
            self._ingress_proof is not None
            and len(proofs) == 1
            and hmac.compare_digest(proofs[0].strip(), self._ingress_proof)
        )
        return {
            **scope,
            "headers": _filter_headers(headers, INGRESS_PROOF_HEADER),
            INGRESS_PROOF_SCOPE_KEY: verified,
        }


def _read_proof(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        value = path.expanduser().read_bytes().strip()
    except OSError:
        return None
    return value if len(value) >= 32 else None


def add_dashboard_auth_proxy_middleware(app: FastAPI) -> None:
    app.add_middleware(cast(Any, DashboardAuthProxyHeaderSanitizerMiddleware))


def _filter_headers(headers: list[tuple[bytes, bytes]], target: bytes) -> list[tuple[bytes, bytes]]:
    return [(name, value) for name, value in headers if name.lower() != target]


__all__ = ["add_dashboard_auth_proxy_middleware", "DashboardAuthProxyHeaderSanitizerMiddleware"]
