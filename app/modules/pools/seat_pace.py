from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.modules.pools.cli_seats import read_seat_accounts
from app.modules.pools.grok_weekly import grok_usage_path, write_reading
from app.modules.pools.pace_curve import Account, pool_pace, recent_burn, timestamp

SEAT_POOLS = ("grok", "cursor", "devin")
HISTORY_HOURS = 25
MIN_CONVERSION_OUTPUT = 100_000


def history_path() -> Path:
    configured = os.environ.get("AGENT_LB_SEAT_PACE_HISTORY")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".agent-lb" / "seats" / "pace-history.json"


@dataclass
class SeatSnapshot:
    samples: list[dict] = field(default_factory=list)
    accounts: dict[str, int] = field(default_factory=dict)
    output: dict[str, int] = field(default_factory=dict)


def seat_pool_for(vendor: str, model: str | None) -> str:
    return "grok" if vendor == "cursor" and model and "grok-" in model else vendor


def _grok_sample() -> dict | None:
    try:
        reading = json.loads(grok_usage_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(reading, dict):
        return None
    used, observed, reset = (
        reading.get("usedPercent"),
        timestamp(reading.get("observedAt")),
        timestamp(reading.get("resetsAt")),
    )
    if not isinstance(used, (int, float)) or isinstance(used, bool) or observed is None or reset is None:
        return None
    return {
        "pool": "grok",
        "account": "grok-weekly",
        "remaining": max(0.0, 100.0 - float(used)),
        "reset": reset.isoformat(),
        "at": observed.isoformat(),
    }


def seat_snapshot() -> SeatSnapshot:
    snapshot = SeatSnapshot()
    seen: set[str] = set()
    for account in read_seat_accounts().accounts:
        if account.vendor not in ("cursor", "devin") or not account.enabled:
            continue
        snapshot.accounts[account.vendor] = snapshot.accounts.get(account.vendor, 0) + 1
        if account.vendor == "cursor":
            snapshot.accounts["grok"] = snapshot.accounts.get("grok", 0) + 1
        for run in account.recent_runs:
            pool = seat_pool_for(account.vendor, run.model)
            snapshot.output[pool] = snapshot.output.get(pool, 0) + (run.tokens_out or 0)
        observed = account.quota.observed_at
        for window in account.quota.windows:
            key = f"{account.plan or account.id}:{window.id}"
            if (
                window.kind != "weekly"
                or key in seen
                or window.remaining_percent is None
                or window.resets_at is None
                or observed is None
            ):
                continue
            seen.add(key)
            snapshot.samples.append(
                {
                    "pool": account.vendor,
                    "account": key,
                    "remaining": float(window.remaining_percent),
                    "reset": window.resets_at.isoformat(),
                    "at": observed.isoformat(),
                }
            )
    grok = _grok_sample()
    if grok is not None:
        snapshot.samples.append(grok)
    return snapshot


def load_history() -> list[dict]:
    try:
        document = json.loads(history_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    samples = document.get("samples") if isinstance(document, dict) else None
    return [s for s in samples or [] if isinstance(s, dict)]


def merged_history(history: list[dict], samples: list[dict], now: datetime) -> list[dict]:
    cutoff = now - timedelta(hours=HISTORY_HOURS)
    keyed = {(s.get("pool"), s.get("account"), s.get("at")): s for s in [*history, *samples]}
    kept = [s for s in keyed.values() if (at := timestamp(s.get("at"))) is not None and cutoff <= at <= now]
    return sorted(kept, key=lambda s: s["at"])


def record_seat_history(now: datetime) -> None:
    write_reading(history_path(), {"samples": merged_history(load_history(), seat_snapshot().samples, now)})


def seat_pools_pace(now: datetime, previous: dict[str, dict], tick: str, fast_hours: float) -> tuple[dict, dict]:
    snapshot = seat_snapshot()
    history = merged_history(load_history(), snapshot.samples, now)
    pools, excluded = {}, {}
    for pool in SEAT_POOLS:
        current = [s for s in snapshot.samples if s["pool"] == pool]
        if not snapshot.accounts.get(pool):
            excluded[pool] = "no_enabled_seat_accounts"
            continue
        if not current:
            excluded[pool] = "no_vendor_quota_window"
            continue
        members = [
            Account(s["account"], s["remaining"], datetime.fromisoformat(s["reset"]), datetime.fromisoformat(s["at"]))
            for s in current
        ]
        rows = [s for s in history if s["pool"] == pool]
        fast = recent_burn(rows, now, {}, fast_hours)
        slow = recent_burn(rows, now, {}, 24)
        pace = pool_pace(members, now, fast=fast, slow=slow, previous=previous.get(pool), tick=tick, last_eligible=0)
        output = snapshot.output.get(pool, 0)
        conversion = slow * 24 / (output / 1e6) if slow and output >= MIN_CONVERSION_OUTPUT else None
        missing = []
        if fast is None:
            missing.append(f"burn_history_{fast_hours:g}h")
        if slow is None:
            missing.append("burn_history_24h")
        if conversion is None:
            missing.append("output_conversion")
        pace.update(
            source="seat_quota",
            windows=sorted(m.id for m in members),
            output_tokens_24h=output,
            output_points_per_million=conversion,
            missing=missing,
        )
        pools[pool] = pace
    return pools, excluded
