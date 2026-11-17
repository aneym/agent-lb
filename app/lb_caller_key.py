from __future__ import annotations

from pathlib import Path

KEY_PREFIX = "sk-clb-"


def caller_key_headers(name: str) -> dict[str, str]:
    path = Path.home() / ".agent-rails/custody/agent-secret" / name
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    return {"Authorization": f"Bearer {value}"} if value.startswith(KEY_PREFIX) else {}
