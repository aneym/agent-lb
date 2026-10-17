from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def routing_table_path() -> Path:
    if os.environ.get("ROUTE_TABLE"):
        return Path(os.environ["ROUTE_TABLE"]).expanduser()
    managed = Path.home() / ".agent-lb/managed/coding-agents/routing-table.json"
    if managed.is_file():
        return managed
    return Path(__file__).resolve().parents[2] / "config/coding-agents/routing-table.json"


def read_routing_table() -> dict[str, Any]:
    try:
        document = json.loads(routing_table_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}
