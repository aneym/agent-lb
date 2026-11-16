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


def load_enrolled() -> dict[str, dict]:
    try:
        data = json.loads(enrollment_path().read_text())
    except (OSError, ValueError):
        return {}
    rows = data.get("accounts") if isinstance(data, dict) and data.get("version") == 1 else None
    if not isinstance(rows, dict):
        return {}
    return {
        key: row
        for key, row in rows.items()
        if isinstance(key, str) and isinstance(row, dict) and not key.startswith(REFUSED_PREFIXES)
    }


def _save(rows: dict[str, dict]) -> None:
    path = enrollment_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".auto-reauth-enrolled-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "accounts": rows}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def enroll(account: dict) -> dict:
    reason = refusal(account)
    if reason:
        raise ValueError(f"{account.get('email')}: {reason}")
    rows = load_enrolled()
    row = {"email": str(account["email"]).lower(), "enrolledAt": datetime.now(UTC).isoformat(timespec="seconds")}
    rows[str(account["accountId"])] = row
    _save(rows)
    return row


def unenroll(account_id: str) -> bool:
    rows = load_enrolled()
    if rows.pop(account_id, None) is None:
        return False
    _save(rows)
    return True
