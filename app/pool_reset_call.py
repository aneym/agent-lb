from __future__ import annotations

import json
import os
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_BASE_URL = "http://127.0.0.1:2455"
RAILS_OFFICE = "mcp:launch:424b86369f27d72d8ecb4d500172a6daee78c383779ab39ad7bbc6eca94b2158"
FACTORY = "mcp:launch:de51e2e9fd174821ec3f65574d06655ad9e11470aa377a41d2cf9e13229ca869"
THREADS = ((RAILS_OFFICE, "note"), (FACTORY, "queue"))


class CallError(Exception):
    pass


def _base() -> str:
    return os.environ.get("AGENT_LB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _request(path: str, timeout: float, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    request = Request(_base() + path, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise CallError(f"HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise CallError(type(exc).__name__) from exc
    if not isinstance(payload, dict):
        raise CallError("malformed response")
    return payload


def _path(account_id: str) -> str:
    return f"/api/accounts/{quote(account_id, safe='')}/rate-limit-reset-credits"


def preview(account_id: str) -> dict[str, Any]:
    found = _request(_path(account_id), 60)
    return {
        "available": found.get("availableCount"),
        "redeemable_now": found.get("redeemableNow"),
        "ineligible_reason": found.get("ineligibleReason"),
    }


def consume_body(override_daily_limit: bool) -> dict[str, Any]:
    return {"overrideDailyLimit": True} if override_daily_limit else {}


def consume(account_id: str, override_daily_limit: bool) -> dict[str, Any]:
    return _request(_path(account_id) + "/consume", 120, consume_body(override_daily_limit))


def send(host: str | None, key: str, text: str) -> list[str]:
    results = []
    for thread, mode in THREADS:
        command = ["t3", "thread", "send", "--mode", mode, "--request-id", f"{key}-{thread[-8:]}", thread, "-"]
        if host:
            remote = "PATH=$HOME/.local/bin:$PATH " + " ".join(command)
            command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, remote]
        try:
            done = subprocess.run(command, input=text, capture_output=True, text=True, timeout=60)
            results.append(f"{thread[-8:]}: exit {done.returncode}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(f"{thread[-8:]}: {type(exc).__name__}")
    return results
