from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from websockets.exceptions import WebSocketException

from app.core.cdp_page import CdpError, CdpPage
from app.lb_caller_key import caller_key_headers

from app.status_cli import NoRedirect

SERVICE_TIMEOUT = 30
_SHOWN_CODE = (
    "(state => { const mark = '#' + state;"
    " const inputs = [...document.querySelectorAll('input, textarea')].map(e => e.value || '');"
    " for (const text of [(document.body && document.body.innerText) || '', ...inputs]) {"
    " const at = text.indexOf(mark); if (at < 0) continue;"
    " const code = text.slice(0, at).match(/[A-Za-z0-9_-]{20,}$/); if (code) return code[0] + mark; }"
    " return null; })(%s)"
)
RETRY_WINDOW = 300


class ServiceError(Exception):
    pass


class ServiceUnavailable(ServiceError):
    pass


def request(base: str, path: str, *, body: dict | None = None, method: str | None = None, cdp: bool = False) -> Any:
    headers = (
        {"Accept": "application/json"}
        if cdp
        else {"Accept": "application/json", **caller_key_headers("agent-lb-key-reauth")}
    )
    if not cdp and (cookie := os.getenv("AGENT_LB_STATUS_COOKIE")):
        headers["Cookie"] = cookie
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method or ("POST" if body is not None else "GET"),
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            request, timeout=2 if cdp else SERVICE_TIMEOUT
        ) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 409 and not cdp:
            try:
                error = json.load(exc).get("error", {})
                if error.get("code") == "oauth_owner_required":
                    raise ServiceError(error.get("message", "OAuth must run on the owner instance")) from None
            except (ValueError, AttributeError):
                pass
        if exc.code in (429, 502, 503, 504):
            raise ServiceUnavailable(f"service returned HTTP {exc.code}") from None
        raise ServiceError(f"service returned HTTP {exc.code}") from None
    except (URLError, OSError, ValueError):
        raise ServiceUnavailable("service unavailable or returned invalid JSON") from None


def retrying(call, what: str):
    deadline = time.monotonic() + RETRY_WINDOW
    delay = 1.0
    while True:
        try:
            return call()
        except ServiceUnavailable as exc:
            if time.monotonic() + delay > deadline:
                raise
            print(f"{what}: {exc}; retrying", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 15)


def cdp_ready(base: str) -> bool:
    try:
        return isinstance(request(base, "/json/version", cdp=True), dict)
    except ServiceError:
        return False


def account_for(base: str, email: str, provider: str | None = None) -> dict | None:
    response = request(base, "/api/accounts")
    if not isinstance(response, dict) or not isinstance(response.get("accounts"), list):
        raise ServiceError("service returned a malformed accounts response")
    return next(
        (
            row
            for row in response["accounts"]
            if row.get("email", "").lower() == email and (provider is None or row.get("provider") == provider)
        ),
        None,
    )


def oauth_state(url: str) -> str | None:
    return (parse_qs(urlsplit(url).query).get("state") or [None])[0]


def callback_pages(cdp: str, prefix: str) -> list[dict]:
    try:
        pages = request(cdp, "/json/list", cdp=True)
    except ServiceError:
        return []
    if not isinstance(pages, list):
        return []
    return [
        page
        for page in pages
        if isinstance(page, dict) and page.get("type") == "page" and str(page.get("url", "")).startswith(prefix)
    ]


def close_page(cdp: str, page: dict) -> None:
    try:
        request(cdp, "/json/close/" + quote(str(page.get("id", "")), safe=""), cdp=True)
    except ServiceError:
        pass


def close_stale_callbacks(cdp: str, prefix: str) -> None:
    for page in callback_pages(cdp, prefix):
        close_page(cdp, page)


def shown_code(port: int, flow: dict) -> str | None:
    expected = oauth_state(flow["authorizationUrl"])
    if not expected:
        return None
    script = _SHOWN_CODE % json.dumps(expected)
    for tab in callback_pages(f"http://127.0.0.1:{port}", flow["callbackUrl"]):
        try:
            page = CdpPage(port, target_id=str(tab.get("id", "")), timeout=10)
        except (CdpError, WebSocketException, OSError, TimeoutError):
            continue
        try:
            shown = page.evaluate(script)
        except (CdpError, WebSocketException, TimeoutError):
            shown = None
        finally:
            page.detach()
        if isinstance(shown, str) and shown:
            return shown
    return None


def send_code(base: str, flow: dict, shown: str) -> bool:
    try:
        reply = request(base, "/api/oauth/manual-callback", body={"flowId": flow["flowId"], "callbackUrl": shown})
    except ServiceUnavailable:
        return False
    except ServiceError as exc:
        reply = {"status": "error", "errorMessage": str(exc)}
    if isinstance(reply, dict) and reply.get("status") == "error":
        print(f"code not accepted ({reply.get('errorMessage')})", file=sys.stderr)
    return True
