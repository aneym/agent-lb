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
    assert decide(pool, NOW).reason == "unknown_burn_rate"


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


@pytest.mark.parametrize("remaining,expected_posts", [(3, 1), (20, 0)])
def test_live_cli_applies_only_redeem(remaining, expected_posts):
    """Real CLI HTTP boundary with a local service stand-in, no production mocks."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    samples = 0
    posts = []
    reset = (datetime.now(UTC) + timedelta(hours=6)).isoformat()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
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
                                    "primaryRemainingPercent": remaining + (1 if samples == 1 else 0),
                                    "secondaryRemainingPercent": 50,
                                },
                                "resetAtPrimary": reset,
                            }
                        ]
                    }
                )
            elif self.path.endswith("/rate-limit-reset-credits"):
                self.reply(
                    {"availableCount": 1, "redeemableNow": True, "credits": [{"id": "grant-a", "status": "available"}]}
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
    assert len(posts) == expected_posts
    if expected_posts:
        assert posts == [("/api/accounts/claude-a/rate-limit-reset-credits/consume", {"creditId": "grant-a"})]
        assert record["redemption"]["status"] == "redeemed"
