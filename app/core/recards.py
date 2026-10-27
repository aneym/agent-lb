from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def registry_path() -> Path:
    return Path(os.getenv("AGENT_LB_STATE_DIR", str(Path.home() / ".agent-lb/state"))) / "recards.json"


def load_recards() -> dict[str, dict]:
    try:
        data = json.loads(registry_path().read_text())
    except (OSError, ValueError):
        return {}
    rows = data.get("recards") if isinstance(data, dict) and data.get("version") == 1 else None
    return {email: row for email, row in rows.items() if isinstance(row, dict)} if isinstance(rows, dict) else {}


def save_recard(email: str, **fields: object) -> dict:
    rows = load_recards()
    row = {**rows.get(email, {}), **fields, "updatedAt": datetime.now(UTC).isoformat(timespec="seconds")}
    rows[email] = row
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".recards-")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"version": 1, "recards": rows}, stream, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return row
