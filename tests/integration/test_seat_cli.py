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

# `seat run --class` holds a reservation first (A4d); this stand-in grants it. The real route and server are in
# test_seat_run_reservation.py.
FAKE_ROUTE = """#!/bin/sh
case "$1" in
  reserve) echo '{"status": "reserved", "reservation_id": "rsv-000000000001", "heartbeat_s": 300}' ;;
  heartbeat|release) echo '{"status": "released"}' ;;
  *) echo grok-9-medium-fast ;;
esac
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

    # A Cursor limit cools only the pool the model draws on (cursor-pool-truth), so
    # cursor-a stays ready account-wide and the vendor pool still counts it.
    seats = read_seat_accounts(tmp_path / "seats" / "state.json")
    by_id = {account.id: account for account in seats.accounts}
    assert by_id["cursor-a"].cooldown_until is None and by_id["cursor-a"].ready
    assert set(by_id["cursor-a"].cooldowns) == {"cursor-models"}
    assert by_id["cursor-b"].ready and by_id["cursor-b"].last_day.tokens_in == 120
    (pool,) = cli_seat_pools(seats)
    assert (pool.id, pool.status, pool.eligible_accounts, pool.observed_runs) == ("cursor", "ok", 2, 2)

    # The cooling account is not retried for that pool; once the other one is limited
    # too the run reports no account, and both hold a cursor-models cooldown.
    limited.write_text("key-a\nkey-b\n")
    second = _seat(env, "run", "--vendor", "cursor", "--model", "grok-latest", "--cwd", str(tmp_path), "--", "x")
    assert second.returncode == 2
    assert [attempt["account"] for attempt in json.loads(second.stdout)["attempts"]] == ["cursor-b"]
    seats = read_seat_accounts(tmp_path / "seats" / "state.json")
    assert all(set(account.cooldowns) == {"cursor-models"} for account in seats.accounts)
    third = _seat(env, "run", "--vendor", "cursor", "--model", "grok-latest", "--cwd", str(tmp_path), "--", "y")
    assert third.returncode == 2, third.stderr
    assert "no cursor account is ready" in third.stderr, third.stderr


def test_cursor_prompt_goes_on_stdin(tmp_path: Path) -> None:
    prompt = "x" * (3 * 1024 * 1024)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    argv_file = tmp_path / "argv.json"
    stdin_file = tmp_path / "stdin.txt"
    cursor = _script(
        tmp_path / "cursor-agent",
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ["models"]:
    print("Available models")
    raise SystemExit(0)
Path(os.environ["FAKE_ARGV_FILE"]).write_text(json.dumps(sys.argv[1:]))
Path(os.environ["FAKE_STDIN_FILE"]).write_text(sys.stdin.read(), encoding="utf-8")
print(json.dumps({"type": "result", "is_error": False, "result": "done"}))
""",
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith(("SEAT_", "ROUTE_", "CURSOR_"))}
    env.update(
        SEAT_HOME=str(tmp_path / "seats"),
        ROUTE_LEDGER=str(tmp_path / "dispatch.jsonl"),
        ROUTE_BIN=str(_script(tmp_path / "route", FAKE_ROUTE)),
        SEAT_CURSOR_BIN=str(cursor),
        FAKE_ARGV_FILE=str(argv_file),
        FAKE_STDIN_FILE=str(stdin_file),
    )
    key_file = tmp_path / "test.key"
    key_file.write_text("fake-key\n")
    key_file.chmod(0o600)
    added = _seat(env, "add", "cursor-test", "--vendor", "cursor", "--api-key-file", str(key_file))
    assert added.returncode == 0, added.stderr

    result = _seat(
        env,
        "run",
        "--vendor",
        "cursor",
        "--model",
        "grok-latest",
        "--cwd",
        str(tmp_path),
        "--prompt-file",
        str(prompt_file),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert prompt not in json.loads(argv_file.read_text())
    assert stdin_file.read_text(encoding="utf-8") == prompt
