"""A Muse seat must surface as a cli_seat pool with unknown (null) headroom."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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
