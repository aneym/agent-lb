from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.browser_profiles import registry_path

REFUSED_PREFIXES = ("25600cac", "86fe250c", "bfb49472")


def enrollment_path() -> Path:
    return registry_path().parent / "auto-reauth-enrolled.json"


def refusal(account: dict) -> str | None:
    account_id = str(account.get("accountId") or "")
    if account_id.startswith(REFUSED_PREFIXES):
        return "another person's account; refused in code"
    if account.get("provider") != "anthropic":
        return f"{account.get('provider')} accounts are not reauthed"
    if account.get("ownerInstance"):
        return f"owned by instance {account['ownerInstance']}"
    return None


def _read() -> tuple[dict[str, dict], dict[str, dict]]:
    try:
        data = json.loads(enrollment_path().read_text())
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(data, dict) or data.get("version") != 1:
        return {}, {}

    def rows(name: str) -> dict[str, dict]:
        found = data.get(name)
        if not isinstance(found, dict):
            return {}
        return {
            key: row
            for key, row in found.items()
            if isinstance(key, str) and isinstance(row, dict) and not key.startswith(REFUSED_PREFIXES)
        }

    return rows("accounts"), rows("dropped")


def load_enrolled() -> dict[str, dict]:
    return _read()[0]


def load_dropped() -> dict[str, dict]:
    return _read()[1]


def _save(rows: dict[str, dict], dropped: dict[str, dict]) -> None:
    path = enrollment_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".auto-reauth-enrolled-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "accounts": rows, "dropped": dropped}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def enroll(account: dict) -> dict:
    reason = refusal(account)
    if reason:
        raise ValueError(f"{account.get('email')}: {reason}")
    rows, dropped = _read()
    row = {"email": str(account["email"]).lower(), "enrolledAt": datetime.now(UTC).isoformat(timespec="seconds")}
    rows[str(account["accountId"])] = row
    dropped.pop(str(account["accountId"]), None)
    _save(rows, dropped)
    return row


def unenroll(account_id: str) -> bool:
    rows, dropped = _read()
    if rows.pop(account_id, None) is None:
        return False
    _save(rows, dropped)
    return True


def drop(account_id: str, why: str) -> dict | None:
    rows, dropped = _read()
    row = rows.pop(account_id, None)
    if row is None:
        return None
    dropped[account_id] = {
        "email": row.get("email"),
        "droppedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "why": why,
    }
    _save(rows, dropped)
    return dropped[account_id]
