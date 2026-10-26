from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.providers.anthropic import ANTHROPIC_PROVIDER_NAME
from app.core.usage.refresh_lock import try_usage_refresh_process_lock
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.usage.refresh_ports import UsageRepositoryPort
from app.modules.usage.refresh_rules import (
    mark_usage_refresh_auth_cooldown,
    usage_refresh_auth_cooldowns,
    usage_refresh_rate_limit_strikes,
)
from app.modules.usage.updater import UsageUpdater


def _remaining(account_id: str) -> float:
    return usage_refresh_auth_cooldowns[account_id] - time.monotonic()


def _reset_usage_refresh_backoff() -> None:
    usage_refresh_auth_cooldowns.clear()
    usage_refresh_rate_limit_strikes.clear()


def test_anthropic_usage_429_backoff_grows_with_jitter_bounds():
    _reset_usage_refresh_backoff()
    account_id = "acct-429"
    mark_usage_refresh_auth_cooldown(account_id, 429)
    first = _remaining(account_id)
    mark_usage_refresh_auth_cooldown(account_id, 429)
    second = _remaining(account_id)
    assert 48 <= first <= 72
    assert 96 <= second <= 144
    assert second > first
    assert usage_refresh_rate_limit_strikes[account_id] == 2
    _reset_usage_refresh_backoff()


class _UsageRepo:
    def __init__(self, latest: UsageHistory) -> None:
        self._latest = latest

    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None:
        del account_id, window
        return self._latest


@pytest.mark.asyncio
async def test_fresh_usage_skip_keeps_429_backoff(monkeypatch):
    monkeypatch.setattr("app.modules.usage.account_sync.TokenEncryptor", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "app.modules.usage.updater.get_settings",
        lambda: SimpleNamespace(
            usage_refresh_enabled=True,
            usage_refresh_interval_seconds=60,
            account_pulse_recovery_interval_seconds=600,
        ),
    )
    _reset_usage_refresh_backoff()
    account = Account(
        id="acct-fresh-429",
        provider=ANTHROPIC_PROVIDER_NAME,
        email="usage@example.test",
        plan_type="max",
        access_token_encrypted=b"token",
        refresh_token_encrypted=b"token",
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )
    latest = UsageHistory(
        account_id=account.id,
        provider=ANTHROPIC_PROVIDER_NAME,
        recorded_at=utcnow(),
        window="primary",
        used_percent=12.0,
        reset_at=int(time.time()) + 3600,
    )
    mark_usage_refresh_auth_cooldown(account.id, 429)
    mark_usage_refresh_auth_cooldown(account.id, 429)
    usage_refresh_auth_cooldowns[account.id] = time.monotonic() - 1
    updater = UsageUpdater(cast(UsageRepositoryPort, _UsageRepo(latest)))
    await updater.refresh_accounts([account], {account.id: latest})
    assert usage_refresh_rate_limit_strikes[account.id] == 2
    _reset_usage_refresh_backoff()


def test_second_process_does_not_take_usage_refresh_lock(tmp_path: Path):
    path = tmp_path / "usage-refresh.lock"
    held = try_usage_refresh_process_lock(path)
    assert held is not None
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.core.usage.refresh_lock import try_usage_refresh_process_lock\n"
            "from pathlib import Path\n"
            "import os\n"
            "print('held' if try_usage_refresh_process_lock(Path(os.environ['LOCK_PATH'])) else 'none')\n",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**env, "LOCK_PATH": str(path)},
    )
    held.close()
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "none"
