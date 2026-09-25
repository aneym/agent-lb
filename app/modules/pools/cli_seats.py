"""Pools for the CLI-only seats (Cursor, Devin), read from the `seat` CLI's state file.

Their traffic never passes through the LB and neither vendor publishes a usage
endpoint for these plans, so there is no window to read. What the LB can serve is
what `clients/seat` observed: which accounts are registered, whether each one's
auth probe passed, which are cooling down after a limit error, and the runs and
tokens each served. Headroom is therefore availability, not a metered percent:
100 while any account can take work, 0 when none can.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import Field

from app.modules.pools.schemas import POOL_STATUS_EXHAUSTED, POOL_STATUS_OK, PoolSummary
from app.modules.shared.schemas import DashboardModel

CLI_SEAT_VENDORS = ("cursor", "devin")
POOL_KIND_CLI_SEAT = "cli_seat"
POOL_SOURCE_SEAT_STATE = "seat_state"
# A state nobody has touched for this long says nothing about auth today.
POOL_SOURCE_SEAT_STATE_STALE = "seat_state_stale"
_STALE_AFTER = timedelta(hours=12)


class SeatAccountUsage(DashboardModel):
    runs: int = 0
    ok: int = 0
    wall_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class SeatAccount(DashboardModel):
    id: str
    vendor: str
    auth: str | None = None
    enabled: bool = True
    auth_ok: bool | None = None
    auth_checked_at: datetime | None = None
    tier: str | None = None
    cooldown_until: datetime | None = None
    last_error_kind: str | None = None
    last_error_at: datetime | None = None
    last_run_at: datetime | None = None
    ready: bool = False
    last_day: SeatAccountUsage = Field(default_factory=SeatAccountUsage)


class SeatAccountsResponse(DashboardModel):
    state_updated_at: datetime | None = None
    source: str
    accounts: list[SeatAccount] = Field(default_factory=list)


def seat_state_path() -> Path:
    configured = os.environ.get("AGENT_LB_SEAT_STATE")
    return Path(configured).expanduser() if configured else Path.home() / ".agent-lb" / "seats" / "state.json"


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _usage(record: dict[str, Any], since: datetime) -> SeatAccountUsage:
    runs = [run for run in record.get("runs") or [] if isinstance(run, dict) and (_ts(run.get("ts")) or since) > since]
    return SeatAccountUsage(
        runs=len(runs),
        ok=sum(1 for run in runs if run.get("ok") is True),
        wall_s=round(sum(float(run.get("wall_s") or 0) for run in runs), 1),
        tokens_in=sum(int(run.get("tokens_in") or 0) for run in runs),
        tokens_out=sum(int(run.get("tokens_out") or 0) for run in runs),
    )


def read_seat_accounts(path: Path | None = None, *, now: datetime | None = None) -> SeatAccountsResponse:
    current = now or datetime.now(timezone.utc)
    state = _read_state(path or seat_state_path())
    if state is None:
        return SeatAccountsResponse(source="missing")
    updated = _ts(state.get("updated_at"))
    stale = updated is None or current - updated > _STALE_AFTER
    accounts: list[SeatAccount] = []
    records = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
    for account_id, record in sorted(records.items()):
        if not isinstance(record, dict) or record.get("vendor") not in CLI_SEAT_VENDORS:
            continue
        cooldown = _ts(record.get("cooldown_until"))
        if cooldown is not None and cooldown <= current:
            cooldown = None
        enabled = record.get("enabled") is not False
        error = record.get("last_error") if isinstance(record.get("last_error"), dict) else {}
        identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
        accounts.append(
            SeatAccount(
                id=str(account_id),
                vendor=record["vendor"],
                auth=record.get("auth"),
                enabled=enabled,
                auth_ok=record.get("auth_ok"),
                auth_checked_at=_ts(record.get("auth_checked_at")),
                tier=identity.get("tier"),
                cooldown_until=cooldown,
                last_error_kind=error.get("kind"),
                last_error_at=_ts(error.get("ts")),
                last_run_at=_ts(record.get("last_run_at")),
                ready=enabled and record.get("auth_ok") is not False and cooldown is None,
                last_day=_usage(record, current - timedelta(hours=24)),
            )
        )
    return SeatAccountsResponse(
        state_updated_at=updated,
        source=POOL_SOURCE_SEAT_STATE_STALE if stale else POOL_SOURCE_SEAT_STATE,
        accounts=accounts,
    )


def cli_seat_pools(seats: SeatAccountsResponse) -> list[PoolSummary]:
    pools: list[PoolSummary] = []
    for vendor in CLI_SEAT_VENDORS:
        members = [account for account in seats.accounts if account.vendor == vendor]
        if not members:
            continue
        ready = [account for account in members if account.ready]
        cooling = [account.cooldown_until for account in members if account.cooldown_until is not None]
        headroom = 100.0 if ready else 0.0
        pools.append(
            PoolSummary(
                id=vendor,
                provider=vendor,
                kind=POOL_KIND_CLI_SEAT,
                accounts=len(members),
                eligible_accounts=len(ready),
                headroom_percent=headroom,
                aggregate_remaining_percent=100.0 * len(ready) / len(members),
                reset_at=None if ready or not cooling else min(cooling),
                status=POOL_STATUS_OK if ready else POOL_STATUS_EXHAUSTED,
                source=seats.source,
                observed_runs=sum(account.last_day.runs for account in members),
                observed_tokens=sum(account.last_day.tokens_in + account.last_day.tokens_out for account in members),
            )
        )
    return pools
