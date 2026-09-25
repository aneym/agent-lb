"""The `seat` CLI's account failover, as the router sees it through /api/pools.

The vendor CLI is the only stand-in: a script that answers like `cursor-agent`, and
reports a usage limit for the API keys listed in a control file. Everything else is
the real path: `seat add` registers the accounts, `seat run` picks, fails over and
writes the ledger and state, and the LB's pool reader turns that state into the
`cursor` pool that `route pick` skips when it is exhausted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.modules.pools.cli_seats import cli_seat_pools, read_seat_accounts

REPO = Path(__file__).resolve().parents[2]
SEAT = REPO / "clients" / "seat"

FAKE_CURSOR = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
key = os.environ.get("CURSOR_API_KEY", "")
if sys.argv[1:] == ["models"]:
    print("Available models")
    raise SystemExit(0)
if key in Path(os.environ["FAKE_LIMITED_KEYS"]).read_text().split():
    print("Error: You've hit your usage limit. Try again in 2 hours.", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps({"type": "result", "is_error": False, "result": "done", "session_id": f"chat-{key}",
                  "usage": {"inputTokens": 120, "outputTokens": 7, "cacheReadTokens": 0}}))
"""

FAKE_ROUTE = """#!/bin/sh
echo grok-9-medium-fast
"""


def _script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def _seat(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SEAT), *args], capture_output=True, text=True, timeout=60, env=env, check=False
    )


def test_limit_fails_over_then_exhausts_the_pool(tmp_path: Path) -> None:
    limited = tmp_path / "limited-keys"
    limited.write_text("key-a\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("SEAT_", "ROUTE_", "CURSOR_"))}
    env.update(
        SEAT_HOME=str(tmp_path / "seats"),
        ROUTE_LEDGER=str(tmp_path / "dispatch.jsonl"),
        ROUTE_BIN=str(_script(tmp_path / "route", FAKE_ROUTE)),
        SEAT_CURSOR_BIN=str(_script(tmp_path / "cursor-agent", FAKE_CURSOR)),
        FAKE_LIMITED_KEYS=str(limited),
    )
    for account_id in ("a", "b"):
        key_file = tmp_path / f"{account_id}.key"
        key_file.write_text(f"key-{account_id}\n")
        key_file.chmod(0o600)
        added = _seat(env, "add", f"cursor-{account_id}", "--vendor", "cursor", "--api-key-file", str(key_file))
        assert added.returncode == 0, added.stderr

    # Both accounts idle: the first registered goes first, hits its limit, and the run
    # completes on the second.
    first = _seat(
        env,
        "run",
        "--vendor",
        "cursor",
        "--model",
        "grok-latest",
        "--class",
        "mechanical",
        "--cwd",
        str(tmp_path),
        "--",
        "rename foo to bar",
    )
    assert first.returncode == 0, first.stderr
    envelope = json.loads(first.stdout)
    assert envelope["account"] == "cursor-b"
    assert [attempt["outcome"] for attempt in envelope["attempts"]] == ["limit", "ok"]
    assert envelope["vendor_session_id"] == "chat-key-b"

    rows = [json.loads(line) for line in (tmp_path / "dispatch.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["dispatch", "closeout"]
    closeout = rows[1]
    assert (closeout["subagent_type"], closeout["model"], closeout["task_class"]) == (
        "cursor-seat",
        "grok-9-medium-fast",
        "mechanical",
    )
    assert (closeout["account"], closeout["ok"], closeout["tokens_in"]) == ("cursor-b", True, 120)
    assert rows[0]["session_id"] == closeout["session_id"]

    seats = read_seat_accounts(tmp_path / "seats" / "state.json")
    by_id = {account.id: account for account in seats.accounts}
    assert by_id["cursor-a"].cooldown_until is not None and not by_id["cursor-a"].ready
    assert by_id["cursor-b"].ready and by_id["cursor-b"].last_day.tokens_in == 120
    (pool,) = cli_seat_pools(seats)
    assert (pool.id, pool.status, pool.eligible_accounts, pool.observed_runs) == ("cursor", "ok", 1, 2)

    # The cooling account is not retried; once the other one is limited too the run
    # reports no account and the pool is exhausted until the earliest cooldown ends.
    limited.write_text("key-a\nkey-b\n")
    second = _seat(env, "run", "--vendor", "cursor", "--model", "grok-latest", "--cwd", str(tmp_path), "--", "x")
    assert second.returncode == 2
    assert [attempt["account"] for attempt in json.loads(second.stdout)["attempts"]] == ["cursor-b"]
    seats = read_seat_accounts(tmp_path / "seats" / "state.json")
    (pool,) = cli_seat_pools(seats)
    assert (pool.status, pool.eligible_accounts) == ("exhausted", 0)
    assert pool.reset_at == min(account.cooldown_until for account in seats.accounts)
