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
    return Path(__file__).resolve().parents[2] / "config/coding-agents/routing-table"


def routing_table_parts(*parts: str) -> dict[str, Any]:
    path = routing_table_path()
    document: dict[str, Any] = {}
    names = parts or sorted(file.stem for file in path.glob("*.json"))
    for file in [path / f"{name}.json" for name in names] if path.is_dir() else [path]:
        value = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or document.keys() & value.keys():
            raise ValueError(f"{file} is not an object or repeats a key")
        document.update(value)
    return document


def read_routing_table(*parts: str) -> dict[str, Any]:
    try:
        return routing_table_parts(*parts)
    except (OSError, UnicodeError, ValueError):
        return {}
