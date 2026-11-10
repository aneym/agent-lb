from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_BASE_URL = "http://127.0.0.1:2455"
SOURCE = "resetCreditsAvailable from the local agent-lb /api/accounts (the count behind agent-lb resets)"
CLEARS_SOURCE = (
    "resetClears from the local agent-lb /api/accounts: the windows named by the clears list of each reset grant "
    "Anthropic's usage endpoint (cedar_ember) shows as held now, the same grants resetCreditsAvailable counts"
)


def _clears(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return sorted({str(window) for window in value})


def held(timeout: float = 20.0) -> tuple[dict[str, int | None], dict[str, list[str] | None], str | None]:
    base = os.environ.get("AGENT_LB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(
            Request(base + "/api/accounts", headers={"Accept": "application/json"}), timeout=timeout
        ) as resp:
            payload: Any = json.loads(resp.read())
    except (OSError, ValueError) as exc:
        return {}, {}, f"{type(exc).__name__}"
    accounts = payload.get("accounts", []) if isinstance(payload, dict) else []
    counts: dict[str, int | None] = {}
    clears: dict[str, list[str] | None] = {}
    for account in accounts:
        if not isinstance(account, dict) or account.get("provider") != "anthropic":
            continue
        value = account.get("resetCreditsAvailable")
        counts[str(account.get("accountId"))] = int(value) if isinstance(value, int) else None
        clears[str(account.get("accountId"))] = _clears(account.get("resetClears"))
    return counts, clears, None
