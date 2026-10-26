from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

TIERS = ("critical", "behind", "on_pace", "ahead", "rich")
BARS = (0.0, 0.7, 0.9, 1.1, 1.3)
FRESH_SECONDS = 1800
PERIOD_HOURS = 168.0
PLAN_FACTORS = {
    "anthropic": {"claude": 1.0},
    "openai": {"pro": 1.0, "plus": 1 / 18},
}
OUTPUT_POINTS_PER_MILLION = {"anthropic": 3.6, "openai": 8.26}
UNUSABLE_STATUSES = frozenset({"paused", "deactivated", "reauth_required"})
HELD_SUBSCRIPTIONS = frozenset({"canceled", "unpaid", "past_due"})


def timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
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


def hours(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


@dataclass(frozen=True)
class Account:
    id: str
    remaining: float
    reset: datetime
    observed: datetime
    factor: float = 1.0
    eligible: bool = True


def same_window(left: Any, right: Any) -> bool:
    left, right = timestamp(left), timestamp(right)
    return left is not None and right is not None and abs((left - right).total_seconds()) < 3600


def plan_factor(provider: str, plan: str | None) -> float | None:
    return PLAN_FACTORS.get(provider, {}).get((plan or "").lower())


def reserve(hours_left: float) -> float:
    return 10.0 * min(1.0, max(0.0, hours_left) / 12.0)


def no_reserve(hours_left: float) -> float:
    return 0.0


def project(
    accounts: list[Account],
    now: datetime,
    burn: float,
    *,
    until: datetime | None = None,
    floor: Callable[[float], float] = reserve,
) -> dict:
    balances = {a.id: a.remaining * a.factor for a in accounts}
    resets = {a.id: a.reset for a in accounts}
    horizon = until or max((a.reset for a in accounts), default=now)
    cursor, refills, shortfall, failed_at = now, [], 0.0, None
    while cursor < horizon:
        events = [horizon]
        for a in accounts:
            events.append(resets[a.id])
            taper = resets[a.id] - timedelta(hours=12)
            if taper > cursor:
                events.append(taper)
        end = min(t for t in events if t > cursor)
        demand = burn * hours(cursor, end)
        for a in sorted(accounts, key=lambda a: (resets[a.id], a.id)):
            spare = max(0.0, balances[a.id] - floor(hours(end, resets[a.id])) * a.factor)
            take = min(demand, spare)
            balances[a.id] -= take
            demand -= take
        if demand > 1e-7:
            shortfall += demand
            failed_at = failed_at or end
        for a in accounts:
            if resets[a.id] == end:
                refills.append(
                    {"account": a.id, "at": end.isoformat(), "before": balances[a.id], "after": 100 * a.factor}
                )
                balances[a.id] = 100.0 * a.factor
                resets[a.id] += timedelta(hours=PERIOD_HOURS)
        cursor = end
    return {
        "feasible": shortfall <= 1e-7,
        "shortfall": shortfall,
        "failed_at": failed_at.isoformat() if failed_at else None,
        "refills": refills,
        "balances": balances,
    }


def sustainable(accounts: list[Account], now: datetime) -> float:
    if not accounts:
        return 0.0
    low = 0.0
    high = sum(100 * a.factor for a in accounts) * 2 / max(min(hours(now, a.reset) for a in accounts), 1 / 60)
    for _ in range(40):
        middle = (low + high) / 2
        if project(accounts, now, middle)["feasible"]:
            low = middle
        else:
            high = middle
    return low


def lose_it_floor(accounts: list[Account], now: datetime) -> float:
    def spare(a: Account) -> float:
        return max(0.0, a.remaining - reserve(hours(now, a.reset))) * a.factor

    return max(
        (sum(spare(a) for a in accounts if a.reset <= at) / hours(now, at) for at in {a.reset for a in accounts}),
        default=0.0,
    )


def short_window_ratio(history: list[dict]) -> float | None:
    pairs: dict[str, list[tuple[datetime, str, float, Any]]] = {}
    for row in history:
        at, used = timestamp(row.get("at")), number(row.get("used"))
        if at is not None and used is not None and row.get("window") in {"5h", "weekly"}:
            pairs.setdefault(row["account"], []).append((at, row["window"], used, row.get("reset")))
    weekly_total, short_total = 0.0, 0.0
    for rows in pairs.values():
        rows.sort(key=lambda r: r[0])
        last: dict[str, tuple[float, Any]] = {}
        for _, window, used, reset in rows:
            prior = last.get(window)
            if prior is not None and same_window(prior[1], reset) and used >= prior[0]:
                if window == "weekly":
                    weekly_total += used - prior[0]
                else:
                    short_total += used - prior[0]
            last[window] = (used, reset)
    if weekly_total < 1 or short_total < 5:
        return None
    return weekly_total / short_total


def short_window_capacity(accounts: list[Account], ratio: float | None) -> float | None:
    if ratio is None:
        return None
    return sum(100.0 * ratio * a.factor / 5.0 for a in accounts if a.eligible)


def step_tier(candidate: int, previous: dict, tick: str | None) -> tuple[int, int]:
    prior = previous.get("tier", "behind")
    prior_index = TIERS.index(prior) if prior in TIERS else 1
    if candidate <= prior_index:
        return candidate, 0
    same_target = previous.get("promotion_target") == TIERS[candidate]
    count = (previous.get("promotion_ticks", 0) if same_target else 0) + int(tick != previous.get("tick"))
    return (candidate, count) if count >= 2 else (prior_index, count)


def pool_pace(
    accounts: list[Account],
    now: datetime,
    *,
    fast: float | None = None,
    slow: float | None = None,
    ungoverned: float | None = None,
    capacity: float | None = None,
    previous: dict | None = None,
    tick: str | None = None,
    incomplete: bool = False,
) -> dict:
    previous = previous or {}
    fresh = [a for a in accounts if 0 <= (now - a.observed).total_seconds() < FRESH_SECONDS]
    usable = [a for a in fresh if a.factor > 0 and a.reset > now]
    stale = incomplete or len(fresh) != len(accounts) or not usable
    simulated = sustainable(usable, now)
    b_star = simulated if capacity is None else min(simulated, capacity)
    eligible = [a for a in usable if a.eligible and a.remaining > reserve(hours(now, a.reset))]
    balance = sum(max(0.0, a.remaining - reserve(hours(now, a.reset))) * a.factor for a in usable)
    floor = lose_it_floor(usable, now)
    if capacity is not None:
        floor = min(floor, capacity)

    def ratio(burn: float | None) -> float | None:
        return b_star / burn if burn is not None and burn > 0 else None

    rho_fast, rho_slow = ratio(fast), ratio(slow)
    known = fast is not None and slow is not None
    ratios = [r for r in (rho_fast, rho_slow) if r is not None]
    rho = min(ratios) if ratios else None
    candidate = next((i for i in reversed(range(len(TIERS))) if rho is not None and rho >= BARS[i]), 0)
    if known and fast == slow == 0 and eligible:
        candidate = len(TIERS) - 1
    next_refill = min((hours(now, a.reset) for a in usable), default=None)
    critical = not stale and (
        len(eligible) <= 1 or (bool(fast) and next_refill is not None and balance / fast < next_refill)
    )
    if critical:
        candidate = 0
    missing = stale or not known
    if missing:
        prior = previous.get("tier", "behind")
        candidate = min(candidate, TIERS.index(prior) if prior in TIERS else 1, 1)
    tier, count = step_tier(candidate, previous, tick)
    return {
        "shadow": True,
        "tier": TIERS[tier],
        "candidate_tier": TIERS[candidate],
        "b_star": b_star,
        "b_star_simulated": simulated,
        "short_window_capacity": capacity,
        "floor": floor,
        "expiring": bool(usable) and b_star > 0 and floor >= 0.8 * b_star,
        "balance": balance,
        "accounts": len(accounts),
        "eligible_accounts": len(eligible),
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
        "refills": project(usable, now, b_star)["refills"],
    }


def recent_burn(history: list[dict], now: datetime, factors: dict[str, float], window: float) -> float | None:
    groups: dict[str, list[tuple[datetime, dict]]] = {}
    for row in history:
        at = timestamp(row.get("at"))
        if at and now - timedelta(hours=window + 1) <= at <= now and number(row.get("remaining")) is not None:
            groups.setdefault(row["account"], []).append((at, row))
    total, measured = 0.0, False
    for account, rows in groups.items():
        rows.sort(key=lambda r: r[0])
        if hours(rows[0][0], rows[-1][0]) < 0.9 * window:
            continue
        decreases = sum(
            max(0.0, left["remaining"] - right["remaining"])
            for (_, left), (_, right) in zip(rows, rows[1:])
            if same_window(left.get("reset"), right.get("reset"))
        )
        total += decreases * factors.get(account, 1.0) / hours(rows[0][0], rows[-1][0])
        measured = True
    return total if measured else None


def sustainable_share(pools: dict[str, dict], conversions: dict[str, float]) -> float | None:
    if "anthropic" not in pools or set(pools) - set(conversions):
        return None
    rates = {name: pace["b_star"] / conversions[name] for name, pace in pools.items() if conversions[name] > 0}
    total = sum(rates.values())
    return rates.get("anthropic", 0.0) / total if total else None


def excluded(summary: Any) -> bool:
    held = summary.subscription is not None and summary.subscription.status in HELD_SUBSCRIPTIONS
    return summary.status in UNUSABLE_STATUSES or bool(summary.deactivation_reason) or held


def account_input(summary: Any) -> Account | None:
    remaining = summary.usage.secondary_remaining_percent if summary.usage else None
    reset = timestamp(summary.reset_at_secondary)
    factor = plan_factor(summary.provider, summary.plan_type)
    if remaining is None or reset is None or factor is None:
        return None
    return Account(
        summary.account_id,
        float(remaining),
        reset,
        timestamp(summary.quota_observed_at) or datetime.min.replace(tzinfo=UTC),
        factor=factor,
        eligible=summary.status == "active",
    )
