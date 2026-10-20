from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.modules.pools.schemas import POOL_STATUS_EXHAUSTED, POOL_STATUS_OK, PoolSummary, PoolWindow
from app.modules.pools.seat_quota import parse_seat_timestamp

GROK_POOL_ID = "grok"
POOL_SOURCE_GROK_VENDOR = "grok_vendor"
POOL_SOURCE_GROK_VENDOR_STALE = "grok_vendor_stale"
USAGE_ENDPOINT = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetSandUsageStatus"
APP_DATA_DIR = Path.home() / "Library/Application Support/Grok Bot"
APP_INFO_PLIST = Path("/Applications/Grok Bot.app/Contents/Info.plist")
_STALE_AFTER = timedelta(minutes=15)


class CredentialError(Exception):
    pass


def grok_usage_path() -> Path:
    configured = os.environ.get("AGENT_LB_GROK_USAGE")
    return Path(configured).expanduser() if configured else Path.home() / ".agent-lb" / "seats" / "grok.json"


def _seat_home() -> Path:
    return Path(os.environ.get("SEAT_HOME") or Path.home() / ".agent-lb" / "seats").expanduser()


def cursor_seat_accounts() -> list[dict[str, Any]]:
    try:
        document = json.loads((_seat_home() / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    accounts = document.get("accounts") if isinstance(document, dict) else None
    return [
        account
        for account in accounts or []
        if isinstance(account, dict) and account.get("vendor") == "cursor" and account.get("enabled", True) is not False
    ]


def _is_ultra(account: dict[str, Any]) -> bool:
    return "ultra" in f"{account.get('tier') or ''} {account.get('plan') or ''}".lower()


def _keychain(service: str, account: str | None = None) -> str:
    command = ["/usr/bin/security", "find-generic-password", "-w", "-s", service]
    if account:
        command += ["-a", account]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise CredentialError(service) from None
    if result.returncode or not result.stdout.strip():
        raise CredentialError(service)
    return result.stdout.strip()


def _seat_credential(account: dict[str, Any]) -> tuple[str, str]:
    if account.get("auth") == "api-key":
        key_file = Path(str(account.get("key_file") or "")).expanduser()
        try:
            value = key_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise CredentialError("key_file") from None
        if not value:
            raise CredentialError("key_file")
        return value, f"seat:{account.get('id')}:key_file"
    return _keychain("cursor-access-token", "cursor-user"), f"seat:{account.get('id')}:keychain"


def _decrypt(value: str, password: str) -> str:
    raw = base64.b64decode(value)
    if raw[:3] != b"v10":
        raise ValueError("unsupported Grok Bot safe storage format")
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, 16)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    padded = decryptor.update(raw[3:]) + decryptor.finalize()
    return padded[: -padded[-1]].decode()


def _app_credential() -> tuple[str, str | None]:
    try:
        stored = json.loads((APP_DATA_DIR / "sand-secrets.json").read_text(encoding="utf-8"))
        accounts = json.loads(stored.get("cursor-accounts") or "null") or {}
        active = (accounts.get("accounts") or {}).get(accounts.get("active"))
    except (OSError, json.JSONDecodeError, AttributeError):
        raise CredentialError("grok_bot_app") from None
    if not isinstance(active, dict) or not active.get("cursor-access-token"):
        raise CredentialError("grok_bot_app")
    password = _keychain("Grok Bot Safe Storage")
    team = active.get("cursor-selected-team-id")
    return _decrypt(active["cursor-access-token"], password), _decrypt(team, password) if team else None


def _app_version() -> str:
    try:
        return str(plistlib.loads(APP_INFO_PLIST.read_bytes())["CFBundleShortVersionString"])
    except (OSError, KeyError, plistlib.InvalidFileException):
        return "0.20.0"


def _post(token: str, team: str | None) -> dict[str, Any]:
    headers = {
        "content-type": "application/json",
        "connect-protocol-version": "1",
        "authorization": "Bearer " + token,
        "x-cursor-client-type": "sand",
        "x-cursor-client-version": _app_version(),
        "x-sand-box-namespace": "prod",
    }
    if team:
        headers["x-cursor-team-id"] = team
    request = urllib.request.Request(USAGE_ENDPOINT, data=b"{}", headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.load(response)
    return body if isinstance(body, dict) else {}


def fetch_grok_usage() -> tuple[dict[str, Any], str]:
    failure: Exception = CredentialError("no_credential")
    for account in cursor_seat_accounts():
        try:
            token, source = _seat_credential(account)
            return _post(token, None), source
        except (CredentialError, urllib.error.HTTPError) as exc:
            failure = exc
    try:
        token, team = _app_credential()
        return _post(token, team), "grok_bot_app"
    except CredentialError:
        raise failure from None


def usage_reading(body: dict[str, Any], observed_at: datetime, source: str) -> dict[str, Any]:
    percent = body.get("usagePercent")
    pooled = body.get("usesPooledEnterpriseAllowance") is True
    return {
        "observedAt": observed_at.isoformat(),
        "credentialSource": source,
        "usedPercent": float(percent) if isinstance(percent, (int, float)) and not pooled else None,
        "resetsAt": body.get("nextResetTimestampUtc"),
        "periodStart": body.get("currentPeriodStart"),
        "hasAvailableUsage": body.get("hasAvailableUsage"),
        "plan": body.get("grokPlanLabel"),
        "cursorPlan": body.get("cursorPlanName"),
        "pooledEnterprise": pooled,
    }


def write_reading(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def collect_grok_usage(path: Path | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    target = path or grok_usage_path()
    current = now or datetime.now(timezone.utc)
    try:
        body, source = fetch_grok_usage()
        reading = usage_reading(body, current, source)
    except (OSError, ValueError, CredentialError, subprocess.SubprocessError) as exc:
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        reading = {
            **(previous if isinstance(previous, dict) else {}),
            "lastError": {"kind": type(exc).__name__, "at": current.isoformat()},
        }
    write_reading(target, reading)
    return reading


def grok_pool(path: Path | None = None, *, now: datetime | None = None) -> PoolSummary | None:
    current = now or datetime.now(timezone.utc)
    try:
        reading = json.loads((path or grok_usage_path()).read_text(encoding="utf-8"))
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
    ultra = sum(1 for account in cursor_seat_accounts() if _is_ultra(account)) or 1
    windows = (
        [
            PoolWindow(
                id="grok-weekly",
                kind="weekly",
                label=f"Grok · {reading.get('plan') or 'subscription'}",
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
        id=GROK_POOL_ID,
        provider=GROK_POOL_ID,
        kind="cli_seat",
        accounts=ultra,
        eligible_accounts=0 if exhausted else ultra,
        headroom_percent=remaining,
        aggregate_remaining_percent=remaining,
        weekly_remaining_percent=remaining,
        weekly_reset_at=resets,
        reset_at=resets,
        window_label="week",
        status=POOL_STATUS_EXHAUSTED if exhausted else POOL_STATUS_OK,
        source=POOL_SOURCE_GROK_VENDOR_STALE if stale else POOL_SOURCE_GROK_VENDOR,
        windows=windows,
        quota_source="vendor" if measured else "unavailable",
        unavailable_reason=None if measured else error.get("kind") or "not_reported",
        percent_used=used if measured else None,
        percent_source="vendor" if measured else None,
    )


if __name__ == "__main__":
    collect_grok_usage()
    pool = grok_pool()
    print(json.dumps(pool.model_dump(mode="json", by_alias=True) if pool else None, indent=2))
