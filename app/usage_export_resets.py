from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_BASE_URL = "http://127.0.0.1:2455"
SOURCE = "resetCreditsAvailable from the local agent-lb /api/accounts (the count behind agent-lb resets)"


def held(timeout: float = 20.0) -> tuple[dict[str, int | None], str | None]:
    base = os.environ.get("AGENT_LB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(
            Request(base + "/api/accounts", headers={"Accept": "application/json"}), timeout=timeout
        ) as resp:
            payload: Any = json.loads(resp.read())
    except (OSError, ValueError) as exc:
        return {}, f"{type(exc).__name__}"
    accounts = payload.get("accounts", []) if isinstance(payload, dict) else []
    counts: dict[str, int | None] = {}
    for account in accounts:
        if not isinstance(account, dict) or account.get("provider") != "anthropic":
            continue
        value = account.get("resetCreditsAvailable")
        counts[str(account.get("accountId"))] = int(value) if isinstance(value, int) else None
    return counts, None
