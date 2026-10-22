from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime

JOBS = ("implement", "mechanical", "explore", "review")


def date(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def label(value):
    text = str(value or "unknown")
    return text if re.fullmatch(r"[A-Za-z0-9_.:/ -]{1,100}", text) else "unknown"


def command(args, errors, name, timeout=25):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            errors.append(f"{name}: exit {result.returncode}")
            return ""
        return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"{name}: {type(exc).__name__}")
        return ""


def document(args, errors, name):
    raw = command(args, errors, name)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        errors.append(f"{name}: invalid JSON")
        return {}


def job(row):
    value = str(row.get("task_class") or row.get("agent_type") or row.get("subagent_type") or "").lower()
    if any(word in value for word in ("verif", "review")):
        return "review"
    return next((kind for kind in JOBS if kind in value), None)
