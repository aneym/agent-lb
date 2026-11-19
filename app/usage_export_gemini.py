from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.modules.pools.gemini_quota import (
    STALE_AFTER,
    GeminiView,
    MeterCounts,
    VendorWindow,
    gemini_view,
    load_reading,
)
from app.modules.pools.gemini_vendor import iso, parse_time

SCHEMA = "agent-lb-gemini/1"
ACCOUNT = "gemini-ultra"
HARNESS = "t3 antigravity"

RULE = (
    "one entry per Gemini login whose seat reading agent-lb holds, read from the reading file the collector writes "
    "every 5 minutes on the host that runs agy (Antigravity) and pushes to the primary. Two sources: Google's model "
    "quota answer, when the vendor read is on and Google reports a headline Gemini 4 Neon window, and agent-lb's meter "
    "of agy's local store, which counts requests and tokens per 5-minute bucket. quota_source is vendor when Google's "
    "headline figures were read at most 45 min ago or no request was metered since, not_reported when Google reports "
    "no headline window or the vendor read is off (the counts then stand alone and no cap is known), and unknown when "
    f"Google's figures are no longer current. state is fresh when the reading is at most {int(STALE_AFTER.total_seconds() // 60)} "
    "min old and either the source is vendor or it is not_reported with a working meter; signed_out when agy's login "
    "is missing or refused; unknown when there is no reading; stale otherwise. usable_now is true only when fresh and "
    "no hold applies: a headline vendor window at 100% before its reset, or a quota error metered in the last 30 min. "
    "Counts cover the headline model; by_model_7d lists every model agy ran. Nothing here calls Google."
)


def _pct(window: VendorWindow | None) -> float | None:
    return round(window.used_percent, 3) if window is not None else None


def _resets(window: VendorWindow | None) -> str | None:
    return iso(window.resets_at) if window is not None and window.resets_at is not None else None


def _when(value: datetime | None) -> str | None:
    return iso(value) if value is not None else None


def _note(view: GeminiView, reading: dict[str, Any] | None) -> str | None:
    if reading is None:
        return "no seat reading on this host"
    if view.state == "signed_out":
        return f"agy login {view.login}"
    if view.state == "stale":
        if view.quota_source == "unknown":
            return "Google's figures are no longer current"
        return "the reading is older than 15 min or the meter could not read agy's store"
    if view.hold == "vendor_cap":
        return "a Google quota window is used up"
    if view.hold == "quota_error":
        return "a quota error was metered in the last 30 min"
    return None


def _by_model(meter: dict[str, Any]) -> list[dict[str, Any]]:
    rows = meter.get("byModel7d")
    return [
        {
            "model": str(row.get("model")),
            "requests": int(row.get("requests") or 0),
            "tokens_in": int(row.get("tokensIn") or 0),
            "tokens_cached": int(row.get("tokensCached") or 0),
            "tokens_out": int(row.get("tokensOut") or 0),
        }
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]


def _buckets(vendor: dict[str, Any]) -> list[dict[str, Any]]:
    rows = vendor.get("buckets")
    return [
        {
            "id": row.get("id"),
            "family": row.get("family"),
            "kind": row.get("kind"),
            "used_pct": row.get("usedPercent"),
            "resets_at": _when(parse_time(row.get("resetsAt"))),
        }
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]


def plan(reading: dict[str, Any] | None, now: datetime, host: str) -> dict[str, Any]:
    view = gemini_view(reading, now)
    source = reading or {}
    vendor = source.get("vendor") if isinstance(source.get("vendor"), dict) else {}
    meter = source.get("meter") if isinstance(source.get("meter"), dict) else {}
    five, day, week = (view.counts.get(name, MeterCounts()) for name in ("5h", "24h", "7d"))
    return {
        "account": ACCOUNT,
        "plan": source.get("plan"),
        "host": source.get("host") or host,
        "harness": HARNESS,
        "quota_source": view.quota_source,
        "five_hour_used_pct": _pct(view.short),
        "five_hour_resets_at": _resets(view.short),
        "weekly_used_pct": _pct(view.weekly),
        "weekly_resets_at": _resets(view.weekly),
        "requests_5h": five.requests,
        "tokens_5h": five.tokens,
        "requests_24h": day.requests,
        "tokens_24h": day.tokens,
        "requests_7d": week.requests,
        "tokens_7d": week.tokens,
        "quota_errors_5h": five.quota_errors,
        "quota_errors_7d": week.quota_errors,
        "capacity_errors_7d": week.capacity_errors,
        "last_request_at": _when(view.last_request_at),
        "last_quota_error_at": _when(view.last_quota_error_at),
        "read_at": _when(view.read_at),
        "metered_at": _when(view.metered_at),
        "checked_at": _when(view.checked_at),
        "state": view.state,
        "stale": view.state != "fresh",
        "usable_now": view.usable_now,
        "note": _note(view, reading),
        "by_model_7d": _by_model(meter),
        "buckets": _buckets(vendor),
    }


def build(generated_at: str, host: str, path: Path | None = None) -> dict[str, Any]:
    now = parse_time(generated_at)
    if now is None:
        raise ValueError(f"generated_at is not a time: {generated_at}")
    reading = load_reading(path)
    collected = reading.get("host") if reading else None
    origin = f", collected on {collected}" if isinstance(collected, str) else ""
    try:
        entry = plan(reading, now, host)
    except Exception as exc:
        entry = {**plan(None, now, host), "note": f"reading could not be read ({type(exc).__name__})"}
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "source": (
            f"agent-lb primary ({host}) seat reading gemini.json{origin} from Google's Antigravity model quota "
            "and agy's local store; read by app.usage_export"
        ),
        "window": {"as_of_utc": generated_at, "stale_minutes": int(STALE_AFTER.total_seconds() // 60)},
        "rule": RULE,
        "plans": [entry],
    }
