"""The pure quota/runway algorithm needs boundary cases; CLI tests use files and subprocesses."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from app.core.clients.anthropic_resets import ResetStatus
from app.modules.accounts.claude_reset_policy import LiveAccount, PolicyState, decide

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def state(remaining=(3, 2), burn=4, opens=6, grant=True):
    return PolicyState(
        live_accounts=[
            LiveAccount(
                id=f"claude-{i}",
                usable=True,
                primary_remaining_pct=pct,
                primary_reset_at=NOW + timedelta(hours=opens),
                weekly_remaining_pct=50,
                burn_rate_pct_per_hour=burn,
                reset_grants_available=int(grant),
                next_grant_id=f"grant-{i}" if grant else None,
                redeemable_now=grant,
            )
            for i, pct in enumerate(remaining)
        ],
        codex={"usable_count": 2, "banked_resets_total": 3},
    )


@pytest.mark.parametrize(
    ("remaining", "burn", "opens", "grant", "action", "reason"),
    [
        ((3, 2), 4, 6, True, "redeem", "cannot_last:"),
        ((3, 20), 4, 6, True, "hold", "above_threshold:claude-1=20"),
        ((4, 4), 0.5, 2, True, "hold", "can_last:8h>=2h"),
        ((3, 2), 4, 6, False, "hold", "no_grant"),
        ((5, 2), 4, 6, True, "hold", "above_threshold:"),
        ((4, 4), 2, 2, True, "hold", "can_last:2h>=2h"),
        ((3, 2), 0, 6, True, "hold", "can_last:infh>=6h"),
    ],
)
def test_policy(remaining, burn, opens, grant, action, reason):
    decision = decide(state(remaining, burn, opens, grant), NOW)
    assert decision.action == action
    assert decision.reason.startswith(reason)
    assert "codex usable=2 banked_resets=3" in decision.summary
    if action == "redeem":
        assert (decision.account_id, decision.grant_id) == ("claude-1", "grant-1")
    else:
        assert decision.account_id is None and decision.grant_id is None


def test_provider_blocker_and_expiry_choice():
    pool = state()
    pool.live_accounts[0].grant_expires_at = NOW + timedelta(hours=1)
    assert decide(pool, NOW).account_id == "claude-0"
    pool.live_accounts[0].reset_status = ResetStatus.model_validate(
        {
            "eligible": True,
            "next_grant_id": "grant-0",
            "grants": [
                {"id": "grant-0", "resets_left": 1, "usable_now": True, "use_requires_limit": True},
            ],
        }
    )
    assert decide(pool, NOW).account_id == "claude-1"
    pool.live_accounts[1].redeemable_now = False
    assert decide(pool, NOW).reason == "no_grant"


def test_no_live_and_missing_burn():
    assert decide(PolicyState(), NOW).reason == "no_live_accounts"
    pool = state()
    pool.live_accounts[0].burn_rate_pct_per_hour = None
    assert decide(pool, NOW).reason == "no_burn_sample"


@pytest.mark.parametrize("remaining,word", [((3, 2), "redeem"), ((20, 20), "hold")])
def test_cli_synthetic_state_never_applies(tmp_path, remaining, word):
    pool = state(remaining)
    for account in pool.live_accounts:
        account.primary_reset_at = datetime.now(UTC) + timedelta(hours=6)
    path = tmp_path / "state.json"
    path.write_text(pool.model_dump_json())
    command = [
        sys.executable,
        "-m",
        "app.cli",
        "resets",
        "policy",
        "--state",
        str(path),
        "--apply",
        "--base-url",
        "http://127.0.0.1:1",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0] == word
    result = subprocess.run([*command, "--json"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["action"] == word and record["synthetic"] is True
    assert "redemption" not in record


@pytest.mark.parametrize(
    "remaining,inventory_status,history,expected_reason",
    [
        (4, 200, "valid", "cannot_last:"),
        (20, 409, "valid", "above_threshold:"),
        (4, 409, "valid", "inventory_unavailable:claude-a"),
        (4, 429, "valid", "inventory_unavailable:claude-a"),
        (4, 200, "missing", "no_burn_sample"),
        (4, 200, "recent", "no_burn_sample"),
        (4, 200, "different_reset", "no_burn_sample"),
        (4, 200, "expired", "no_burn_sample"),
    ],
)
def test_live_cli_applies_only_redeem(tmp_path, remaining, inventory_status, history, expected_reason):
    """Real CLI HTTP boundary with a local service stand-in, no production mocks."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    samples = 0
    inventory_requests = []
    posts = []
    now = datetime.now(UTC)
    reset = (now + timedelta(hours=6)).isoformat()
    samples_path = tmp_path / "samples.json"
    age = {"recent": timedelta(minutes=1), "expired": timedelta(hours=49)}.get(history, timedelta(minutes=10))
    stored_reset = (now + timedelta(hours=7)).isoformat() if history == "different_reset" else reset
    stored = (
        [
            {
                "account_id": "claude-a",
                "window": "primary",
                "reset_at": stored_reset,
                "remaining_pct": 9,
                "observed_at": (now - age).isoformat(),
            }
        ]
        if history != "missing"
        else []
    )
    # Too-recent observations must not displace the eligible ten-minute sample.
    stored.append(
        {
            "account_id": "claude-a",
            "window": "primary",
            "reset_at": reset,
            "remaining_pct": remaining,
            "observed_at": (now - timedelta(seconds=30)).isoformat(),
        }
    )
    stored.append(
        {
            "account_id": "claude-a",
            "window": "primary",
            "reset_at": reset,
            "remaining_pct": 99,
            "observed_at": (now - timedelta(hours=49)).isoformat(),
        }
    )
    samples_path.write_text(json.dumps(stored))
    expected_posts = int(expected_reason == "cannot_last:")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            nonlocal samples
            if self.path == "/health/ready":
                self.reply({"status": "ready"})
            elif self.path == "/api/accounts":
                samples += 1
                self.reply(
                    {
                        "accounts": [
                            {
                                "accountId": "claude-a",
                                "provider": "anthropic",
                                "status": "active",
                                "usage": {
                                    "primaryRemainingPercent": remaining,
                                },
                                "resetAtPrimary": reset,
                            }
                        ]
                    }
                )
            elif self.path.endswith("/rate-limit-reset-credits"):
                inventory_requests.append(self.path)
                self.reply(
                    {"availableCount": 1, "redeemableNow": True, "credits": [{"id": "grant-a", "status": "available"}]},
                    status=inventory_status,
                )
            else:
                self.reply({})

        def do_POST(self):
            posts.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.reply({"status": "redeemed", "code": "reset", "windowsReset": 1})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.cli",
                    "resets",
                    "policy",
                    "--apply",
                    "--samples",
                    str(samples_path),
                    "--json",
                    "--base-url",
                    f"http://127.0.0.1:{server.server_port}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            server.shutdown()
            worker.join()
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["action"] == ("redeem" if expected_posts else "hold")
    assert record["reason"].startswith(expected_reason)
    assert samples == 1
    assert len(inventory_requests) == int(remaining < 5)
    persisted = json.loads(samples_path.read_text())
    assert all(datetime.fromisoformat(sample["observed_at"]) >= now - timedelta(hours=48) for sample in persisted)
    assert persisted[-1]["remaining_pct"] == remaining
    assert datetime.fromisoformat(persisted[-1]["reset_at"]) == datetime.fromisoformat(reset)
    assert len(posts) == expected_posts
    if expected_posts:
        assert posts == [("/api/accounts/claude-a/rate-limit-reset-credits/consume", {"creditId": "grant-a"})]
        assert record["redemption"]["status"] == "redeemed"


def test_above_threshold_never_requests_inventory(tmp_path, monkeypatch, capsys):
    """A pool-wide threshold must gate inventory even for the first low account.

    The brief requires a fake _request to detect any attempted inventory call.
    """
    from argparse import Namespace

    from app import reset_cli

    requests = []
    reset = (datetime.now(UTC) + timedelta(hours=6)).isoformat()

    def request(base_url, path, timeout, **kwargs):
        requests.append(path)
        pytest.fail(f"inventory must not be requested above threshold: {path}")

    def observe(**kwargs):
        return {
            "accounts": [
                {
                    "account_id": account_id,
                    "provider": "anthropic",
                    "usable": "usable",
                    "primary": {"remaining_percent": remaining, "reset_at": reset},
                    "weekly": {"remaining_percent": None, "reset_at": None},
                }
                for account_id, remaining in [("low", 4), ("high", 20)]
            ]
        }

    monkeypatch.setattr(reset_cli, "_request", request)
    monkeypatch.setattr("app.status_cli.observe", observe)
    reset_cli.run(
        Namespace(
            resets_command="policy",
            state=None,
            samples=str(tmp_path / "samples.json"),
            apply=True,
            base_url="http://127.0.0.1:2455",
            timeout=1,
            json=True,
        )
    )
    record = json.loads(capsys.readouterr().out)
    assert record["action"] == "hold"
    assert record["reason"] == "above_threshold:high=20"
    assert requests == []
