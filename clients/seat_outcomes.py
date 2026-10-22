from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from clients.seat_constants import (
    AUTH_RE,
    DEFAULT_COOLDOWN,
    ERROR_CHARS,
    LIMIT_RE,
    MODEL_UNAVAILABLE_RE,
    RETRY_AFTER_RE,
    TRANSIENT_RE,
)
from clients.seat_quota import muse_quota
from clients.seat_state import now, read_json


def classify(text: str) -> str | None:
    if AUTH_RE.search(text):
        return "auth"
    if LIMIT_RE.search(text):
        return "limit"
    if MODEL_UNAVAILABLE_RE.search(text):
        return "model_unavailable"
    if TRANSIENT_RE.search(text):
        return "transient_infra"
    return None


def cooldown_until(text: str) -> datetime:
    match = RETRY_AFTER_RE.search(text)
    if match:
        unit = {"second": "seconds", "minute": "minutes", "hour": "hours"}[match.group(2).lower()]
        return now() + timedelta(**{unit: int(match.group(1))})
    return now() + DEFAULT_COOLDOWN


def parse_outcome(vendor: str, completed: subprocess.CompletedProcess[str], export_file: Path) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "ok": False,
        "result": None,
        "session_id": None,
        "tokens_in": None,
        "tokens_out": None,
        "cache_read_tokens": None,
        "error": None,
    }
    stderr = completed.stderr.strip()
    if vendor == "muse":
        terminal = None
        seen = set()
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_id = event.get("id")
            if event_id and event_id in seen:
                continue
            if event_id:
                seen.add(event_id)
            payload = payload if isinstance(payload := event.get("payload"), dict) else {}
            stream = stream if isinstance(stream := event.get("stream"), dict) else {}
            if stream.get("kind") == "session":
                outcome["session_id"] = stream.get("id")
            if event.get("payload_type", "").startswith("run.terminal."):
                terminal = payload.get("terminal")
                outcome["result"] = payload.get("text")
                outcome["error"] = payload.get("reason")
            usage = payload.get("usage")
            if isinstance(usage, dict):
                for field, names in (("tokens_in", ("input_tokens", "inputTokens")),
                                     ("tokens_out", ("output_tokens", "outputTokens")),
                                     ("cache_read_tokens", ("cache_read_tokens", "cacheReadTokens"))):
                    value = next((usage[n] for n in names if n in usage), None)
                    if isinstance(value, int) and value >= 0:
                        outcome[field] = (outcome[field] or 0) + value
        outcome["ok"] = completed.returncode == 0 and terminal == "completed"
        if not outcome["ok"]:
            outcome["error"] = (str(outcome["error"] or stderr or "Muse emitted no completed terminal"))[:ERROR_CHARS]
        outcome["quota"] = muse_quota(completed.stdout)
        outcome["usage_source"] = "exec-json" if outcome["tokens_in"] is not None else "unavailable"
        return outcome
    if vendor == "cursor":
        try:
            envelope = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {}
        except json.JSONDecodeError:
            envelope = {}
        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        outcome.update(
            result=envelope.get("result"),
            session_id=envelope.get("session_id"),
            tokens_in=usage.get("inputTokens"),
            tokens_out=usage.get("outputTokens"),
            cache_read_tokens=usage.get("cacheReadTokens"),
            ok=completed.returncode == 0 and envelope.get("is_error") is False,
        )
        if not outcome["ok"]:
            outcome["error"] = (
                stderr or str(envelope.get("result") or completed.stdout.strip()) or f"exit {completed.returncode}"
            )[:ERROR_CHARS]
        return outcome
    export = read_json(export_file, {}) if export_file.is_file() else {}
    metrics = isinstance(export, dict) and export.get("final_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    outcome.update(
        result=completed.stdout.strip(),
        session_id=export.get("session_id") if isinstance(export, dict) else None,
        tokens_in=metrics.get("total_prompt_tokens"),
        tokens_out=metrics.get("total_completion_tokens"),
        cache_read_tokens=metrics.get("total_cached_tokens"),
        ok=completed.returncode == 0 and bool(completed.stdout.strip()),
    )
    if not outcome["ok"]:
        outcome["error"] = (stderr or completed.stdout.strip() or f"exit {completed.returncode}")[:ERROR_CHARS]
    return outcome
