"""A Cursor monthly limit must leave the account's other pool routable."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.modules.pools.cli_seats import cursor_budget_pools, read_seat_accounts
from tests.unit.test_seat_cursor_ids import REPO, cli  # noqa: F401


@pytest.mark.parametrize("shared_plan", [False, True])
def test_seat_pool_cooldown(cli, tmp_path: Path, shared_plan: bool) -> None:  # noqa: F811
    run, spawn = cli
    cursor = tmp_path / "cursor"
    cursor.write_text(
        cursor.read_text().replace(
            "print(json.dumps({'is_error': False, 'result': 'done'}))",
            "prompt = sys.stdin.read()\n"
            "error = ('You\\'ve hit your usage limit. Your usage limits will reset when your monthly cycle ends on 10/30/2026' "
            "if prompt == 'limit' else ('authentication required' if prompt == 'auth' else None))\n"
            "print(json.dumps({'is_error': bool(error), 'result': error or 'done'}))\n"
            "sys.exit(1 if error else 0)",
        )
    )
    registry = tmp_path / "seats" / "accounts.json"
    document = json.loads(registry.read_text())
    document["accounts"][0].update(tier="Ultra", cycle_day=30)
    if shared_plan:
        document["accounts"].append(
            {
                "id": "fixture-two",
                "vendor": "cursor",
                "auth": "login",
                "tier": "Ultra",
                "cycle_day": 30,
            }
        )
    registry.write_text(json.dumps(document))
    if shared_plan:
        for account_id in ("fixture", "fixture-two"):
            result = run("seat", "set", account_id, "--plan", "fixture-plan")
            assert result.returncode == 0, result.stderr

    def dispatch(model: str, prompt: str):
        return run("seat", "run", "--vendor", "cursor", "--model", model, "--", prompt)

    sonnet = "claude-sonnet-5-5-high"
    assert dispatch(sonnet, "limit").returncode != 0
    assert spawn.exists()
    spawn.unlink()
    result = dispatch("composer-2.5", "success")
    assert result.returncode == 0, result.stderr
    assert spawn.exists()
    spawn.unlink()
    assert dispatch(sonnet, "success").returncode != 0
    assert not spawn.exists()

    path = tmp_path / "seats" / "state.json"
    record = json.loads(path.read_text())["accounts"]["fixture"]
    assert record["cooldowns"]["cursor-other"] == "2026-10-30T00:00:00Z"
    assert not record.get("cooldown_until")
    seats = read_seat_accounts(path)
    config = json.loads((REPO / "config/coding-agents/routing-table.json").read_text())["cli_pools"]
    pools = {
        pool.id: pool.model_dump(mode="json", by_alias=True)
        for pool in cursor_budget_pools(seats, config, now=datetime.now(timezone.utc))
    }
    assert pools["cursor-models"]["eligibleAccounts"] == (2 if shared_plan else 1)
    assert pools["cursor-other"]["accounts"] == (2 if shared_plan else 1)
    assert pools["cursor-other"]["budgetUsd"] == config["cursor"]["pools"][1]["budget_usd_by_tier"]["Ultra"]
    assert pools["cursor-models"]["status"] != "exhausted"
    assert pools["cursor-other"]["eligibleAccounts"] == 0
    assert pools["cursor-other"]["percentUsed"] == 100.0
    assert pools["cursor-other"]["percentSource"] == "vendor"
    if shared_plan:
        assert all(account.plan == "fixture-plan" for account in seats.accounts)
        assert all("cursor-other" in account.cooldowns for account in seats.accounts)
    assert seats.model_dump(mode="json", by_alias=True)["accounts"][0]["cooldowns"] == {
        "cursor-other": "2026-10-30T00:00:00Z",
    }

    assert dispatch("composer-2.5", "auth").returncode != 0
    assert spawn.exists()
    spawn.unlink()
    record = json.loads(path.read_text())["accounts"]["fixture"]
    assert record["cooldown_until"]
    assert dispatch("composer-2.5", "success").returncode != 0
    assert not spawn.exists()
