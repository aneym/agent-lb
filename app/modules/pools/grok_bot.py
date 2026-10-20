from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.modules.pools.schemas import POOL_STATUS_EXHAUSTED, POOL_STATUS_OK, PoolSummary, PoolWindow
from app.modules.pools.seat_quota import parse_seat_timestamp

GROK_BOT_POOL_ID = "grok-bot"
POOL_SOURCE_GROK_BOT_APP = "grok_bot_app"
POOL_SOURCE_GROK_BOT_APP_STALE = "grok_bot_app_stale"
USAGE_ENDPOINT = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetSandUsageStatus"
APP_DATA_DIR = Path.home() / "Library/Application Support/Grok Bot"
APP_INFO_PLIST = Path("/Applications/Grok Bot.app/Contents/Info.plist")
_STALE_AFTER = timedelta(minutes=15)


def grok_bot_usage_path() -> Path:
    configured = os.environ.get("AGENT_LB_GROK_BOT_USAGE")
    return Path(configured).expanduser() if configured else Path.home() / ".agent-lb" / "seats" / "grok-bot.json"


def _decrypt(value: str, password: str) -> str:
    raw = base64.b64decode(value)
    if raw[:3] != b"v10":
        raise ValueError("unsupported Grok Bot safe storage format")
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, 16)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    padded = decryptor.update(raw[3:]) + decryptor.finalize()
    return padded[: -padded[-1]].decode()


def _active_account() -> dict[str, Any] | None:
    try:
        stored = json.loads((APP_DATA_DIR / "sand-secrets.json").read_text(encoding="utf-8"))
        accounts = json.loads(stored.get("cursor-accounts") or "null") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    active = (accounts.get("accounts") or {}).get(accounts.get("active"))
    return active if isinstance(active, dict) and active.get("cursor-access-token") else None


def _app_version() -> str:
    try:
        return str(plistlib.loads(APP_INFO_PLIST.read_bytes())["CFBundleShortVersionString"])
    except (OSError, KeyError, plistlib.InvalidFileException):
        return "0.20.0"


def fetch_grok_bot_usage(account: dict[str, Any]) -> dict[str, Any]:
    password = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-w", "-s", "Grok Bot Safe Storage"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.rstrip("\n")
    headers = {
        "content-type": "application/json",
        "connect-protocol-version": "1",
        "authorization": "Bearer " + _decrypt(account["cursor-access-token"], password),
        "x-cursor-client-type": "sand",
        "x-cursor-client-version": _app_version(),
        "x-sand-box-namespace": "prod",
    }
    if account.get("cursor-selected-team-id"):
        headers["x-cursor-team-id"] = _decrypt(account["cursor-selected-team-id"], password)
    request = urllib.request.Request(USAGE_ENDPOINT, data=b"{}", headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.load(response)
    return body if isinstance(body, dict) else {}


def usage_reading(body: dict[str, Any], observed_at: datetime) -> dict[str, Any]:
    percent = body.get("usagePercent")
    pooled = body.get("usesPooledEnterpriseAllowance") is True
    return {
        "observedAt": observed_at.isoformat(),
        "usedPercent": float(percent) if isinstance(percent, (int, float)) and not pooled else None,
        "resetsAt": body.get("nextResetTimestampUtc"),
        "periodStart": body.get("currentPeriodStart"),
        "hasAvailableUsage": body.get("hasAvailableUsage"),
        "plan": body.get("grokPlanLabel"),
        "cursorPlan": body.get("cursorPlanName"),
        "pooledEnterprise": pooled,
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
    os.replace(handle.name, path)


def collect_grok_bot_usage(path: Path | None = None, *, now: datetime | None = None) -> dict[str, Any] | None:
    account = _active_account()
    if account is None:
        return None
    target = path or grok_bot_usage_path()
    current = now or datetime.now(timezone.utc)
    try:
        reading = usage_reading(fetch_grok_bot_usage(account), current)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        reading = {
            **(previous if isinstance(previous, dict) else {}),
            "lastError": {"kind": type(exc).__name__, "at": current.isoformat()},
        }
    _write(target, reading)
    return reading


def grok_bot_pool(path: Path | None = None, *, now: datetime | None = None) -> PoolSummary | None:
    current = now or datetime.now(timezone.utc)
    try:
        reading = json.loads((path or grok_bot_usage_path()).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(reading, dict):
        return None
    observed = parse_seat_timestamp(reading.get("observedAt"))
    resets = parse_seat_timestamp(reading.get("resetsAt"))
    used = reading.get("usedPercent")
    measured = isinstance(used, (int, float)) and observed is not None
    remaining = max(0.0, 100.0 - used) if measured else None
    exhausted = reading.get("hasAvailableUsage") is False or (
        measured and used >= 100 and (resets is None or resets > current)
    )
    stale = observed is None or current - observed > _STALE_AFTER
    error = reading.get("lastError") if isinstance(reading.get("lastError"), dict) else {}
    windows = (
        [
            PoolWindow(
                id="weekly",
                kind="weekly",
                label=reading.get("plan") or "Grok Bot",
                used_percent=used,
                remaining_percent=remaining,
                resets_at=resets,
                measured_accounts=1,
                source="vendor",
            )
        ]
        if measured
        else []
    )
    return PoolSummary(
        id=GROK_BOT_POOL_ID,
        provider=GROK_BOT_POOL_ID,
        kind="cli_seat",
        accounts=1,
        eligible_accounts=0 if exhausted else 1,
        headroom_percent=remaining,
        aggregate_remaining_percent=remaining,
        weekly_remaining_percent=remaining,
        weekly_reset_at=resets,
        reset_at=resets,
        window_label="week",
        status=POOL_STATUS_EXHAUSTED if exhausted else POOL_STATUS_OK,
        source=POOL_SOURCE_GROK_BOT_APP_STALE if stale else POOL_SOURCE_GROK_BOT_APP,
        windows=windows,
        quota_source="vendor" if measured else "unavailable",
        unavailable_reason=None if measured else error.get("kind") or "not_reported",
        percent_used=used if measured else None,
        percent_source="vendor" if measured else None,
    )


if __name__ == "__main__":
    collect_grok_bot_usage()
    pool = grok_bot_pool()
    print(json.dumps(pool.model_dump(mode="json", by_alias=True) if pool else None, indent=2))
