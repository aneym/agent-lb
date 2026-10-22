from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from clients.seat_constants import RUNS_KEPT
from clients.seat_state import ledger_path, now, parse_ts, write_json


def _count(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number >= 0 else 0


def record_lease_usage(account: dict[str, Any], row: dict[str, Any]) -> None:
    line = {
        "ts": row.get("ts"),
        "model": row.get("model"),
        "ok": bool(row.get("ok")),
        "wall_s": row.get("wall_s"),
        "tokens_in": _count(row.get("tokens_in")),
        "tokens_out": _count(row.get("tokens_out")),
        "cache_read_tokens": _count(row.get("cache_read_tokens")),
    }
    try:
        lease_dir = Path(account["lease_dir"])
        log_path = lease_dir / "usage.jsonl"
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(line, sort_keys=True) + "\n")
        totals = {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0}
        last_model = row.get("model")
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            for field in totals:
                try:
                    totals[field] += int(item.get(field) or 0)
                except (TypeError, ValueError, OverflowError):
                    continue
            if item.get("model"):
                last_model = item["model"]
        write_json(lease_dir / "usage.json", {"model": last_model, **totals})
    except (OSError, UnicodeError, TypeError, ValueError):
        return


def record_run(record: dict[str, Any], run: dict[str, Any]) -> None:
    run = dict(run)
    for field in ("tokens_in", "tokens_out", "cache_read"):
        if run.get(field) is None:
            continue
        try:
            run[field] = int(run.get(field) or 0)
        except (TypeError, ValueError, OverflowError):
            run[field] = 0
    record["runs"] = ((record.get("runs") or []) + [run])[-RUNS_KEPT:]
    if run.get("model"):
        day = (parse_ts(run.get("ts")) or now()).astimezone(ZoneInfo("America/New_York")).date().isoformat()
        bucket = (
            record.setdefault("daily", {})
            .setdefault(day, {})
            .setdefault(run["model"], {"runs": 0, "ok": 0, "tokens_in": 0, "tokens_out": 0, "cache_read": 0})
        )
        bucket["runs"] += 1
        if "ok" in bucket:
            bucket["ok"] += int(run.get("ok") is True)
        bucket["tokens_known"] = bucket.get("tokens_known", bucket["runs"] == 1 or
            (bucket.get("tokens_in", 0) + bucket.get("tokens_out", 0)) > 0) and all(
            run.get(field) is not None for field in ("tokens_in", "tokens_out"))
        for field in ("tokens_in", "tokens_out", "cache_read"):
            bucket[field] += int(run.get(field) or 0)


def release_usage(model: str | None, path: str | None) -> dict[str, Any]:
    empty = {"model": None, "tokens_in": None, "tokens_out": None, "cache_read": None}
    if not model or not path:
        return empty
    try:
        usage = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(usage, dict):
            return empty
        counts = {
            field: usage.get(source)
            for field, source in (
                ("tokens_in", "tokens_in"),
                ("tokens_out", "tokens_out"),
                ("cache_read", "cache_read_tokens"),
            )
        }
        counts = {
            field: int(value) if type(value) is float and value.is_integer() else value
            for field, value in counts.items()
        }
        if any(value is not None and (type(value) is not int or value < 0) for value in counts.values()):
            return empty
        return {"model": model, **counts}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return empty


def append_ledger(rows: list[dict[str, Any]]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
