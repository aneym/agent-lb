from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.modules.pools.gemini_vendor import parse_time
from app.modules.pools.schemas import (
    POOL_STATUS_EXHAUSTED,
    POOL_STATUS_LOW,
    POOL_STATUS_OK,
    PoolSummary,
    PoolWindow,
)

GEMINI_POOL_ID = "gemini"
READING_SCHEMA = "agent-lb-gemini-reading/1"
POOL_SOURCE_VENDOR = "gemini_vendor"
POOL_SOURCE_OBSERVED = "gemini_observed"
STALE_AFTER = timedelta(minutes=15)
VENDOR_CURRENT = timedelta(minutes=45)
QUOTA_ERROR_HOLD = timedelta(minutes=30)
METER_BUCKET = timedelta(minutes=5)
LOW_HEADROOM = 25.0
MODEL_NAME = "Gemini 4 Neon"
SIGNED_OUT_LOGINS = frozenset({"signed_out", "refused"})


def gemini_usage_path() -> Path:
    return Path(os.environ.get("AGENT_LB_GEMINI_USAGE", "~/.agent-lb/seats/gemini.json")).expanduser()


def load_reading(path: Path | None = None) -> dict[str, Any] | None:
    try:
        reading = json.loads((path or gemini_usage_path()).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return reading if isinstance(reading, dict) else None


@dataclass(frozen=True)
class MeterCounts:
    requests: int = 0
    tokens: int = 0
    quota_errors: int = 0
    capacity_errors: int = 0


@dataclass(frozen=True)
class VendorWindow:
    kind: str
    used_percent: float
    resets_at: datetime | None


@dataclass(frozen=True)
class GeminiView:
    state: str
    quota_source: str
    usable_now: bool
    hold: str | None
    reported: bool
    short: VendorWindow | None
    weekly: VendorWindow | None
    counts: dict[str, MeterCounts] = field(default_factory=dict)
    checked_at: datetime | None = None
    read_at: datetime | None = None
    metered_at: datetime | None = None
    last_request_at: datetime | None = None
    last_quota_error_at: datetime | None = None
    login: str | None = None


def _counts(windows: Any, name: str) -> MeterCounts:
    row = windows.get(name) if isinstance(windows, dict) else None
    if not isinstance(row, dict):
        return MeterCounts()
    return MeterCounts(
        requests=int(row.get("requests") or 0),
        tokens=int(row.get("tokensIn") or 0) + int(row.get("tokensCached") or 0) + int(row.get("tokensOut") or 0),
        quota_errors=int(row.get("quotaErrors") or 0),
        capacity_errors=int(row.get("capacityErrors") or 0),
    )


def _vendor_window(windows: list[Any], kinds: tuple[str, ...], now: datetime) -> VendorWindow | None:
    for window in windows:
        if isinstance(window, dict) and window.get("kind") in kinds:
            used = window.get("usedPercent")
            if not isinstance(used, (int, float)):
                continue
            resets = parse_time(window.get("resetsAt"))
            if resets is not None and resets <= now:
                used = 0.0
            return VendorWindow(kind=str(window["kind"]), used_percent=float(used), resets_at=resets)
    return None


def gemini_view(reading: dict[str, Any] | None, now: datetime) -> GeminiView:
    if reading is None:
        return GeminiView("unknown", "unknown", False, None, False, None, None)
    vendor = reading.get("vendor") if isinstance(reading.get("vendor"), dict) else {}
    meter = reading.get("meter") if isinstance(reading.get("meter"), dict) else {}
    windows = vendor.get("windows") if vendor.get("reported") and isinstance(vendor.get("windows"), list) else []
    short = _vendor_window(windows, ("session", "other"), now)
    weekly = _vendor_window(windows, ("weekly",), now)
    reported = short is not None or weekly is not None
    checked_at = parse_time(reading.get("checkedAt"))
    read_at = parse_time(vendor.get("observedAt"))
    last_request = parse_time(meter.get("lastRequestAt"))
    last_error = parse_time(meter.get("lastQuotaErrorAt"))
    login = vendor.get("login")
    current = read_at is not None and (
        now - read_at <= VENDOR_CURRENT or last_request is None or last_request < read_at
    )
    quota_source = ("vendor" if current else "unknown") if reported else "not_reported"
    meter_ok = meter.get("ok") is True
    if login in SIGNED_OUT_LOGINS:
        state = "signed_out"
    elif (
        checked_at is not None
        and now - checked_at <= STALE_AFTER
        and (quota_source == "vendor" or (quota_source == "not_reported" and meter_ok))
    ):
        state = "fresh"
    else:
        state = "stale"
    capped = any(
        w is not None and w.used_percent >= 100.0 and (w.resets_at is None or w.resets_at > now)
        for w in (short, weekly)
    )
    erred = last_error is not None and now - (last_error + METER_BUCKET) <= QUOTA_ERROR_HOLD
    hold = "vendor_cap" if capped else "quota_error" if erred else None
    return GeminiView(
        state=state,
        quota_source=quota_source,
        usable_now=state == "fresh" and hold is None,
        hold=hold,
        reported=reported,
        short=short,
        weekly=weekly,
        counts={name: _counts(meter.get("windows"), name) for name in ("5h", "24h", "7d")},
        checked_at=checked_at,
        read_at=read_at,
        metered_at=parse_time(meter.get("scannedAt")),
        last_request_at=last_request,
        last_quota_error_at=last_error,
        login=login if isinstance(login, str) else None,
    )


def _pool_window(
    window_id: str, kind: str, label: str, figure: VendorWindow | None, counts: MeterCounts, fresh: bool
) -> PoolWindow:
    if figure is None:
        return PoolWindow(
            id=window_id,
            kind=kind,
            label=label,
            measured_accounts=0,
            source="observed",
            observed_requests=counts.requests,
            observed_tokens=counts.tokens,
        )
    return PoolWindow(
        id=window_id,
        kind=kind,
        label=label,
        used_percent=figure.used_percent,
        remaining_percent=max(0.0, 100.0 - figure.used_percent),
        resets_at=figure.resets_at,
        measured_accounts=1,
        source="vendor" if fresh else "vendor_observed",
        observed_requests=counts.requests,
        observed_tokens=counts.tokens,
    )


def gemini_pool(path: Path | None = None, *, now: datetime | None = None) -> PoolSummary | None:
    current = now or datetime.now(timezone.utc)
    reading = load_reading(path)
    if reading is None:
        return None
    view = gemini_view(reading, current)
    fresh = view.state == "fresh"
    vendor = fresh and view.quota_source == "vendor"
    short_label = "Reported quota" if view.short and view.short.kind == "other" else "5-hour"
    if view.reported:
        labels = (f"{MODEL_NAME} · {short_label}", f"{MODEL_NAME} · Weekly")
    else:
        labels = (f"{MODEL_NAME} · last 5 hours (rolling)", f"{MODEL_NAME} · last 7 days (rolling)")
    windows = [
        _pool_window("gemini-5h", "session", labels[0], view.short, view.counts["5h"], vendor),
        _pool_window("gemini-weekly", "weekly", labels[1], view.weekly, view.counts["7d"], vendor),
    ]
    figures = [w for w in (view.short, view.weekly) if w is not None] if vendor else []
    remaining = min((100.0 - w.used_percent for w in figures), default=None)
    remaining = max(0.0, remaining) if remaining is not None else None
    busiest = max(figures, key=lambda w: w.used_percent, default=None)
    if not view.usable_now:
        status = POOL_STATUS_EXHAUSTED
    elif remaining is not None and remaining < LOW_HEADROOM:
        status = POOL_STATUS_LOW
    else:
        status = POOL_STATUS_OK
    if fresh:
        reason = None if vendor else "not_reported"
    else:
        reason = "credential_unavailable" if view.state == "signed_out" else "vendor_error"
    source = POOL_SOURCE_VENDOR if view.reported else POOL_SOURCE_OBSERVED
    return PoolSummary(
        id=GEMINI_POOL_ID,
        provider=GEMINI_POOL_ID,
        kind="cli_seat",
        accounts=1,
        eligible_accounts=1 if view.usable_now else 0,
        headroom_percent=remaining,
        aggregate_remaining_percent=remaining,
        five_hour_remaining_percent=max(0.0, 100.0 - view.short.used_percent) if vendor and view.short else None,
        five_hour_reset_at=view.short.resets_at if vendor and view.short else None,
        weekly_remaining_percent=max(0.0, 100.0 - view.weekly.used_percent) if vendor and view.weekly else None,
        weekly_reset_at=view.weekly.resets_at if vendor and view.weekly else None,
        reset_at=busiest.resets_at if busiest else None,
        window_label="week" if vendor else None,
        status=status,
        source=source if fresh else f"{source}_stale",
        windows=windows,
        quota_source="vendor" if vendor else "unavailable",
        unavailable_reason=reason,
        percent_used=busiest.used_percent if busiest else None,
        percent_source="vendor" if busiest else None,
        observed_runs=view.counts["24h"].requests,
        observed_tokens=view.counts["24h"].tokens,
    )
