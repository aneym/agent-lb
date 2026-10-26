from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

TIERS = ("critical", "behind", "on_pace", "ahead", "rich")
BARS = (0.0, 0.7, 0.9, 1.1, 1.3)


def timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        try:
            return timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def number(value: Any) -> float | None:
    if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


@dataclass(frozen=True)
class Account:
    id: str
    remaining: float
    reset: datetime
    observed: datetime
    factor: float = 1.0
    eligible: bool = True
    credits: int = 0
    credit_expiry: datetime | None = None
    period_hours: float = 168.0


def reserve(hours: float) -> float:
    return 10.0 * min(1.0, max(0.0, hours) / 12.0)


def simulate(accounts: list[Account], now: datetime, burn: float) -> dict:
    if not accounts:
        return {"feasible": False, "refills": []}
    balances = {a.id: a.remaining * a.factor for a in accounts}
    credits = {a.id: a.credits for a in accounts}
    horizon = max(a.reset for a in accounts)
    resets = {a.id: a.reset for a in accounts}
    cursor = now
    refills = []
    while cursor < horizon:
        events = [horizon]
        for a in accounts:
            events.append(resets[a.id])
            taper = resets[a.id] - timedelta(hours=12)
            if taper > cursor:
                events.append(taper)
            if credits[a.id] and a.credit_expiry and a.credit_expiry > cursor:
                events.append(a.credit_expiry)
        end = min(t for t in events if t > cursor)
        if any(credits.values()):
            end = min(end, cursor + timedelta(minutes=1))
        hours = (end - cursor).total_seconds() / 3600
        available = {}
        for a in accounts:
            left = (resets[a.id] - end).total_seconds() / 3600
            available[a.id] = max(0.0, balances[a.id] - reserve(left) * a.factor)
        demand = burn * hours
        ordered = sorted(accounts, key=lambda a: (a.credit_expiry or resets[a.id], resets[a.id], a.id))
        for a in ordered:
            take = min(demand, available[a.id])
            balances[a.id] -= take
            demand -= take
            if demand > 1e-9 and credits[a.id] and (a.credit_expiry is None or end <= a.credit_expiry):
                floor = reserve((resets[a.id] - end).total_seconds() / 3600) * a.factor
                while credits[a.id] and demand > 1e-9:
                    credits[a.id] -= 1
                    balances[a.id] = 100.0 * a.factor
                    take = min(demand, balances[a.id] - floor)
                    balances[a.id] -= take
                    demand -= take
        if demand > 1e-7:
            return {"feasible": False, "failed_at": end.isoformat(), "refills": refills}
        for a in accounts:
            if resets[a.id] == end:
                refills.append(
                    {"account": a.id, "at": end.isoformat(), "before": balances[a.id], "after": 100 * a.factor}
                )
                balances[a.id] = 100.0 * a.factor
                resets[a.id] += timedelta(hours=a.period_hours)
        cursor = end
    return {"feasible": True, "refills": refills}


def sustainable(accounts: list[Account], now: datetime) -> float:
    if not accounts:
        return 0.0
    low, high = 0.0, sum(100 * a.factor * (1 + a.credits) for a in accounts)
    hours = min((a.reset - now).total_seconds() / 3600 for a in accounts)
    high /= max(hours, 1e-6)
    for _ in range(36):
        middle = (low + high) / 2
        if simulate(accounts, now, middle)["feasible"]:
            low = middle
        else:
            high = middle
    return low


def pool_pace(
    accounts: list[Account],
    now: datetime,
    *,
    fast: float | None = None,
    slow: float | None = None,
    ungoverned: float | None = None,
    previous: dict | None = None,
    tick: str | None = None,
    incomplete: bool = False,
) -> dict:
    previous = previous or {}
    stale = any(not 0 <= (now - a.observed).total_seconds() < 1800 for a in accounts)
    usable = [
        a
        for a in accounts
        if a.eligible and a.factor > 0 and a.reset > now and 0 <= (now - a.observed).total_seconds() < 1800
    ]
    missing = incomplete or stale or not usable or fast is None or slow is None
    b_star = sustainable(usable, now)
    balance = sum(max(0.0, a.remaining - reserve((a.reset - now).total_seconds() / 3600)) * a.factor for a in usable)
    floor = max(
        (
            sum(
                max(0.0, a.remaining - reserve((a.reset - now).total_seconds() / 3600)) * a.factor
                for a in usable
                if a.reset <= reset
            )
            / ((reset - now).total_seconds() / 3600)
            for reset in {a.reset for a in usable}
        ),
        default=0.0,
    )

    def ratio(burn):
        return b_star / burn if burn is not None and burn > 0 else None

    rho_fast, rho_slow = ratio(fast), ratio(slow)
    ratios = [r for r in (rho_fast, rho_slow) if r is not None]
    rho = min(ratios) if ratios else None
    known = fast is not None and slow is not None
    candidate = next((i for i in reversed(range(5)) if rho is not None and rho >= BARS[i]), 0)
    if known and fast == slow == 0 and usable:
        candidate = 4
    next_refill = min(((a.reset - now).total_seconds() / 3600 for a in usable), default=None)
    critical = not missing and (
        len(usable) <= 1 or (fast is not None and fast > 0 and next_refill is not None and balance / fast < next_refill)
    )
    if critical:
        candidate = 0
    prior = previous.get("tier", "behind")
    prior_index = TIERS.index(prior) if prior in TIERS else 1
    if missing:
        candidate = min(prior_index, 1)
    count = 0
    tier = candidate
    if candidate > prior_index:
        count = previous.get("promotion_ticks", 0) + int(tick != previous.get("tick"))
        if previous.get("promotion_target") != TIERS[candidate]:
            count = int(tick != previous.get("tick"))
        tier = candidate if count >= 2 else prior_index
    return {
        "shadow": True,
        "tier": TIERS[tier],
        "candidate_tier": TIERS[candidate],
        "b_star": b_star,
        "floor": floor,
        "expiring": bool(usable) and floor >= 0.8 * b_star,
        "balance": balance,
        "eligible_accounts": len(usable),
        "next_refill_h": next_refill,
        "b_fast": fast,
        "b_slow": slow,
        "rho_fast": rho_fast,
        "rho_slow": rho_slow,
        "rho": rho,
        "stale": missing,
        "critical": critical,
        "ungoverned": ungoverned,
        "lead_budget": None if ungoverned is None else max(0.0, 0.75 * (b_star - ungoverned)),
        "promotion_ticks": count,
        "promotion_target": TIERS[candidate],
        "tick": tick,
        "refills": simulate(usable, now, b_star)["refills"],
    }


def slow_burn(history: list[dict], now: datetime) -> float | None:
    groups: dict[str, list[tuple[datetime, dict]]] = {}
    for row in history:
        at = timestamp(row.get("at"))
        if at and now - timedelta(hours=25) <= at <= now and number(row.get("remaining")) is not None:
            groups.setdefault(row["account"], []).append((at, row))
    total, measured = 0.0, False
    for rows in groups.values():
        rows.sort(key=lambda r: r[0])
        if not rows or (rows[-1][0] - rows[0][0]).total_seconds() < 23 * 3600:
            continue
        decreases = 0.0
        for (_, left), (_, right) in zip(rows, rows[1:]):
            if left.get("reset") == right.get("reset"):
                decreases += max(0.0, left["remaining"] - right["remaining"]) * right.get("factor", 1.0)
        total += decreases / ((rows[-1][0] - rows[0][0]).total_seconds() / 3600)
        measured = True
    return total if measured else None


def sustainable_share(pools: dict[str, dict], conversions: dict[str, float]) -> float | None:
    if set(pools) - set(conversions) or any(value <= 0 for value in conversions.values()):
        return None
    rates = {name: pace["b_star"] / conversions[name] for name, pace in pools.items()}
    total = sum(rates.values())
    return rates.get("anthropic", 0.0) / total if total else None


def account_input(summary: Any) -> Account | None:
    remaining = summary.usage.secondary_remaining_percent if summary.usage else None
    if remaining is None or summary.reset_at_secondary is None:
        return None
    held = summary.subscription and summary.subscription.status in {"canceled", "unpaid", "past_due"}
    eligible = summary.status not in {"paused", "deactivated", "reauth_required", "rate_limited", "quota_exceeded"}
    eligible = eligible and not held and not summary.deactivation_reason
    reset = timestamp(summary.reset_at_secondary)
    if reset is None:
        return None
    return Account(
        summary.account_id,
        float(remaining),
        reset,
        timestamp(summary.quota_observed_at) or datetime.min.replace(tzinfo=UTC),
        eligible=bool(eligible),
    )
