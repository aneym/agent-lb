from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.providers import normalize_provider_name
from app.db.models import AccountStatus
from app.modules.accounts.schemas import AccountSummary
from app.modules.pools.gemini_quota import QUOTA_ERROR_HOLD, gemini_view, load_reading
from app.modules.pools.service import is_pool_usable

DEFAULT_SEED_CEILING: dict[str, Any] = {
    "schema": "neon-ceiling/1",
    "tokens_5h": 880_000_000,
    "requests_5h": 3900,
    "observed_at": "2026-11-25T17:55:00Z",
    "source": "Historical Gemini 4 Neon observation (reference only; fallback triggers on actual quota exhaustion)",
}

FALLBACK_ORDER: list[dict[str, Any]] = [
    {"providerInstanceId": "gemini", "model": "gemini-4-neon-medium", "options": []},
    {
        "providerInstanceId": "codex",
        "model": "sol-latest",
        "options": [{"id": "reasoningEffort", "value": "high"}],
    },
]

_FIVE_HOURS = 300


def _iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _used_percent(summary: AccountSummary, window: str) -> float | None:
    usage = summary.usage
    if usage is None:
        return None
    remaining = getattr(usage, f"{window}_remaining_percent", None)
    if remaining is None:
        return None
    return round(max(0.0, min(100.0, 100.0 - float(remaining))), 2)


def _codex_plans(summaries: list[AccountSummary]) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for summary in summaries:
        if normalize_provider_name(summary.provider) != "openai":
            continue
        if summary.status != AccountStatus.ACTIVE.value or not is_pool_usable(summary):
            continue
        five_hour_used = (
            _used_percent(summary, "primary")
            if summary.window_minutes_primary == _FIVE_HOURS
            else _used_percent(summary, "secondary")
            if summary.window_minutes_secondary == _FIVE_HOURS
            else None
        )
        weekly_used = (
            _used_percent(summary, "secondary")
            if summary.window_minutes_secondary is not None and summary.window_minutes_secondary != _FIVE_HOURS
            else _used_percent(summary, "primary")
            if summary.window_minutes_primary is not None and summary.window_minutes_primary != _FIVE_HOURS
            else None
        )
        if weekly_used is None or not (0.0 <= weekly_used < 90.0):
            continue
        plan = (summary.plan_type or "").lower()
        if plan == "plus" and (five_hour_used is None or not (0.0 <= five_hour_used < 80.0)):
            continue
        plans.append(
            {
                "account": summary.email or summary.account_id,
                "plan": summary.plan_type,
                "status": summary.status,
                "state": "fresh",
                "weekly_used_pct": weekly_used,
                "five_hour_used_pct": five_hour_used,
            }
        )
    return plans


def build_neon_pick(
    summaries: list[AccountSummary],
    *,
    now: datetime | None = None,
    reading: dict[str, Any] | None = None,
) -> dict[str, Any]:
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    raw = reading if reading is not None else load_reading()
    codex = _codex_plans(summaries)
    limit = dict(DEFAULT_SEED_CEILING)
    rows: list[dict[str, Any]] = []
    if raw is not None:
        view = gemini_view(raw, at)
        counts_5h = view.counts.get("5h")
        tokens_5h = counts_5h.tokens if counts_5h is not None else None
        requests_5h = counts_5h.requests if counts_5h is not None else None
        quota_errors_5h = counts_5h.quota_errors if counts_5h is not None else None
        pace = (
            max(tokens_5h / limit["tokens_5h"], requests_5h / limit["requests_5h"])
            if tokens_5h is not None and requests_5h is not None
            else None
        )
        fresh = view.state == "fresh"
        vendor_exhausted = view.hold == "vendor_cap"
        signed_out = view.state == "signed_out"
        recent_error = (
            view.last_quota_error_at is not None
            and timedelta(0) <= at - view.last_quota_error_at <= QUOTA_ERROR_HOLD
        )
        usable_now = fresh and view.usable_now and not vendor_exhausted and not signed_out
        exhausted = (fresh and not usable_now) or vendor_exhausted or signed_out or (not view.usable_now and recent_error)
        rows.append(
            {
                "account": raw.get("account") or "gemini",
                "fresh": fresh,
                "usable_now": usable_now,
                "exhausted": exhausted,
                "vendor_exhausted": vendor_exhausted,
                "signed_out": signed_out,
                "recent_quota_error": recent_error,
                "pace": pace,
                "tokens_5h": tokens_5h,
                "requests_5h": requests_5h,
                "quota_errors_5h": quota_errors_5h,
                "last_quota_error_at": _iso_z(view.last_quota_error_at),
            }
        )

    usable = [row for row in rows if row["usable_now"]]
    lead = usable[0] if usable else (rows[0] if rows else {})
    if usable:
        chosen = FALLBACK_ORDER[0]
        if lead["pace"] is not None:
            why = f"Neon is usable (5-hour usage at {lead['pace']:.1%} of reference ceiling; no quota exhaustion)."
        else:
            why = "Neon is usable (5-hour counts unavailable; no quota exhaustion)."
    elif rows and (any(row["fresh"] for row in rows) or all(row["exhausted"] for row in rows)):
        if any(row["vendor_exhausted"] for row in rows):
            reason = "Neon vendor quota is exhausted"
        elif any(row["signed_out"] for row in rows):
            reason = "Neon login is signed out"
        elif any(row["recent_quota_error"] for row in rows):
            reason = "Neon has a recent quota error"
        else:
            reason = "Neon is not usable now"
        chosen = FALLBACK_ORDER[1]
        count_label = "plan" if len(codex) == 1 else "plans"
        headroom = (
            f"{len(codex)} Codex {count_label} below the limits"
            if codex
            else "no fresh Codex plan below the limits"
        )
        why = f"{reason}; using Codex Sol at high effort ({headroom})."
    else:
        chosen = FALLBACK_ORDER[0]
        why = "Neon has no fresh reading; staying on Neon until quota exhaustion is observed."

    public_rows = [
        {
            key: row[key]
            for key in (
                "account",
                "fresh",
                "usable_now",
                "pace",
                "tokens_5h",
                "requests_5h",
                "quota_errors_5h",
                "last_quota_error_at",
            )
        }
        for row in rows
    ]
    return {
        "schema": "neon-pick/1",
        "at": _iso_z(at),
        "pick": chosen,
        "why": why,
        "neon": {
            "usable_now": lead.get("usable_now", False),
            "pace": lead.get("pace"),
            "tokens_5h": lead.get("tokens_5h"),
            "requests_5h": lead.get("requests_5h"),
            "quota_errors_5h": lead.get("quota_errors_5h"),
            "last_quota_error_at": lead.get("last_quota_error_at"),
            "ceiling": limit,
            "plans": public_rows,
        },
        "fallback_order": FALLBACK_ORDER,
        "codex": {"plans": codex},
    }
