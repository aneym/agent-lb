from __future__ import annotations

import base64
import json
from typing import cast


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str, *, plan_type: str = "plus") -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status = 200
        self.reason = "OK"
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, *, content_type=None):
        return self._payload

    def __await__(self):
        async def _return_self():
            return self

        return _return_self().__await__()


class _JsonSession:
    def __init__(self, response: _JsonResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        json=None,
        headers: dict[str, str] | None = None,
        timeout=None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._response


def _session_call_url(session: _JsonSession) -> str:
    return cast(str, session.calls[0]["url"])


def _session_call_json(session: _JsonSession) -> dict[str, object]:
    return cast(dict[str, object], session.calls[0]["json"])
