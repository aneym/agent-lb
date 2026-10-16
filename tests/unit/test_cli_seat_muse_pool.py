from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.modules.pools.cli_seats import cli_seat_pools, read_seat_accounts


def _write_state(tmp_path: Path) -> Path:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "accounts": {
                    "muse-main": {
                        "vendor": "muse",
                        "auth": "login",
                        "enabled": True,
                        "auth_ok": True,
                    }
                },
            }
        )
    )
    return state


def test_muse_seat_pool_has_null_headroom(tmp_path: Path) -> None:
    seats = read_seat_accounts(_write_state(tmp_path))
    assert [account.id for account in seats.accounts] == ["muse-main"]

    pools = cli_seat_pools(seats)
    assert len(pools) == 1
    pool = pools[0]
    assert pool.id == "muse"
    assert pool.provider == "muse"
    assert pool.accounts == 1
    assert pool.eligible_accounts == 1
    assert pool.headroom_percent is None
    assert pool.aggregate_remaining_percent is None
    assert pool.observed_tokens is None


def test_muse_unknown_tokens_and_observed_quota_survive_state_read(tmp_path: Path) -> None:
    state = _write_state(tmp_path)
    payload = json.loads(state.read_text())
    record = payload["accounts"]["muse-main"]
    day = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    record["runs"] = [
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": "muse-wonder-1.0",
            "ok": True,
            "tokens_in": None,
            "tokens_out": None,
        }
    ]
    record["daily"] = {day: {"muse-wonder-1.0": {"runs": 1, "ok": 1, "tokens_known": False}}}
    record["quota"] = {
        "source": "vendor_observed",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "primaryWindowId": "session",
        "windows": [
            {
                "id": "session",
                "kind": "session",
                "label": "5-hour",
                "durationMins": 300,
                "usedPercent": 12,
                "remainingPercent": 88,
                "source": "vendor_observed",
            }
        ],
    }
    state.write_text(json.dumps(payload))
    seats = read_seat_accounts(state)
    account = seats.accounts[0]
    assert account.today.runs == 1
    assert account.today.tokens_known is False
    assert account.today.tokens_in is None
    assert account.today.value_usd is None
    pool = cli_seat_pools(seats)[0]
    assert pool.headroom_percent is None
    assert pool.windows[0].remaining_percent == 88
    assert pool.quota_source == "vendor_observed"
