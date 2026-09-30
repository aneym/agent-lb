"""Cursor dollars: leased runs record model and tokens, and the LB prices them into two monthly pools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.modules.pools import cli_seats

REPO = Path(__file__).resolve().parents[2]
SEAT = REPO / "clients" / "seat"
TABLE = REPO / "config" / "coding-agents" / "routing-table.json"
FAKE_CURSOR = """#!/usr/bin/env python3
import sys
if sys.argv[1:] == ['models']:
    print('Available models')
    raise SystemExit(0)
raise SystemExit(1)
"""


def seat(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SEAT), *args], env=env, capture_output=True, text=True, timeout=60, check=False)


def test_leased_cursor_runs_are_priced_into_two_monthly_pools(tmp_path: Path) -> None:
    fake = tmp_path / "cursor-agent"
    fake.write_text(FAKE_CURSOR)
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SEAT_", "ROUTE_", "CURSOR_", "XDG_"))}
    env.update(HOME=str(tmp_path), SEAT_HOME=str(tmp_path / "seats"), ROUTE_LEDGER=str(tmp_path / "ledger.jsonl"),
               SEAT_CURSOR_BIN=str(fake))
    key = tmp_path / "main.key"
    key.write_text("SENTINEL-" + uuid.uuid4().hex + "\n")
    key.chmod(0o600)
    added = seat(env, "add", "cursor-main", "--vendor", "cursor", "--api-key-file", str(key))
    assert added.returncode == 0, added.stderr
    tiered = seat(env, "set", "cursor-main", "--tier", "Ultra", "--cycle-day", "1")
    assert tiered.returncode == 0, tiered.stderr

    for model, tokens_in, tokens_out in (
        ("grok-4.7-medium", 1_000_000, 100_000),
        ("composer-2.5", 10_000_000, 1_000_000),
        ("claude-sonnet-5-5-high", 2_000_000, 200_000),
    ):
        leased = seat(env, "lease", "--vendor", "cursor", "--for", f"unit-{model}", "--ttl", "600",
                      "--out", str(tmp_path / uuid.uuid4().hex), "--json")
        assert leased.returncode == 0, leased.stderr
        usage = tmp_path / f"{model}.usage.json"
        usage.write_text(json.dumps({"vendor": "cursor", "tokens_in": tokens_in, "tokens_out": tokens_out,
                                     "cache_read_tokens": 0, "session_id": "s", "is_error": False}))
        released = seat(env, "release", json.loads(leased.stdout)["lease"], "--outcome", "ok",
                        "--model", model, "--usage-file", str(usage), "--json")
        assert released.returncode == 0, released.stderr

    config = json.loads(TABLE.read_text(encoding="utf-8"))["cli_pools"]
    now = datetime.now(timezone.utc)
    seats = cli_seats.read_seat_accounts(tmp_path / "seats" / "state.json", now=now)
    pools = {pool.id: pool for pool in cli_seats.cursor_budget_pools(seats, config, now=now)}

    models, other = pools["cursor-models"], pools["cursor-other"]
    # Grok 1M in at $2 + 0.1M out at $6 = $2.60; Composer 10M in at $0.50 + 1M out at $2.50 = $7.50.
    assert models.spent_usd == pytest.approx(10.10)
    # Cursor publishes no size for its own models' pool: no budget, so no remaining figure to pace on.
    assert (models.budget_usd, models.monthly_remaining_percent, models.unbudgeted_accounts) == (None, None, 1)
    # Sonnet through Cursor: 2M in at $2 + 0.2M out at $10 = $6.00 of the Ultra tier's $400.
    assert other.spent_usd == pytest.approx(6.00)
    assert other.budget_usd == 400
    assert other.monthly_remaining_percent == pytest.approx(98.5)
    assert other.burn24h_percent == pytest.approx(1.5)
    assert (other.window_label, other.status) == ("month", "ok")
    assert other.cycle_reset_at is not None and other.cycle_reset_at > now
