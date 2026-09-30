"""Cursor transport and account misses recover at the seat CLI boundary."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODEL = "grok-4.7-low"


@pytest.fixture
def dispatch(tmp_path: Path):
    seats = tmp_path / "seats"
    seats.mkdir()
    accounts = []
    for account_id in ("account-a", "account-b"):
        key = tmp_path / account_id
        key.write_text(account_id)  # Synthetic identity, not a credential.
        accounts.append({"id": account_id, "vendor": "cursor", "auth": "api-key", "key_file": str(key)})
    (seats / "accounts.json").write_text(json.dumps({"accounts": accounts}))
    cursor = tmp_path / "cursor"
    cursor.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if 'models' in sys.argv:\n"
        "    sys.exit(0)\n"
        "account = os.environ['CURSOR_API_KEY']\n"
        "calls = Path(os.environ['CALLS'])\n"
        "previous = calls.read_text().splitlines() if calls.exists() else []\n"
        "with calls.open('a') as stream:\n"
        "    stream.write(json.dumps({'account': account, 'env': {k: v for k, v in os.environ.items() "
        "if k.lower() in ('https_proxy', 'http_proxy', 'all_proxy', 'no_proxy')}}) + '\\n')\n"
        "scenario = os.environ['SCENARIO']\n"
        "error = None\n"
        "if scenario == 'both_missing' or (scenario == 'missing' and account == 'account-a'):\n"
        "    error = 'Cannot use this model: grok-4.7-low. Available models: composer-2.5'\n"
        "elif scenario == 'transient' and not previous:\n"
        "    error = os.environ['TRANSIENT_ERROR']\n"
        "elif scenario == 'infra_exhausted':\n"
        "    error = 'Failed to reach the Cursor API. Check that your proxy'\n"
        "print(json.dumps({'is_error': bool(error), 'result': error or 'done'}))\n"
        "sys.exit(1 if error else 0)\n"
    )
    cursor.chmod(0o755)
    route = tmp_path / "route"
    route.write_text(f"#!{sys.executable}\nimport sys\nprint(sys.argv[-1])\n")
    route.chmod(0o755)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("SEAT_", "ROUTE_", "CURSOR_"))}
    env.update(SEAT_HOME=str(seats), ROUTE_BIN=str(route), SEAT_CURSOR_BIN=str(cursor),
               ROUTE_LEDGER=str(tmp_path / "ledger"), CALLS=str(tmp_path / "calls"),
               SEAT_RETRY_JITTER_S="0,0")

    def run(scenario="success", argv=None, **extra):
        result = subprocess.run(
            [sys.executable, str(REPO / "clients/seat"), *(argv or ["run", "--vendor", "cursor",
             "--model", MODEL, "--", "fixture prompt"])],
            env={**env, "SCENARIO": scenario, **extra}, capture_output=True, text=True,
            timeout=65, check=False,
        )
        calls = tmp_path / "calls"
        rows = [json.loads(row) for row in calls.read_text().splitlines()] if calls.exists() else []
        return result, rows

    return run, seats


def test_local_proxies_removed_corporate_proxy_and_no_proxy_kept(dispatch):
    run, _ = dispatch
    result, calls = run(HTTPS_PROXY="http://127.0.0.1:2458", https_proxy="http://localhost:2458",
                        HTTP_PROXY="http://127.0.0.1:2458", http_proxy="localhost:2458",
                        ALL_PROXY="socks5://localhost:2458", all_proxy="http://corporate.example:8080",
                        NO_PROXY="internal.example", no_proxy="internal.example")
    assert result.returncode == 0, result.stderr
    assert calls[0]["env"] == {"all_proxy": "http://corporate.example:8080",
                               "NO_PROXY": "internal.example", "no_proxy": "internal.example"}
    assert result.stderr.count("dropped local proxy variables") == 1


def test_model_miss_fails_over_and_is_skipped_until_expiry(dispatch):
    run, seats = dispatch
    result, calls = run("missing")
    assert result.returncode == 0, result.stderr
    assert [row["account"] for row in calls] == ["account-a", "account-b"]
    envelope = json.loads(result.stdout)
    assert [attempt["outcome"] for attempt in envelope["attempts"]] == ["model_unavailable", "ok"]
    state = json.loads((seats / "state.json").read_text())
    until = datetime.fromisoformat(state["accounts"]["account-a"]["model_unavailable"][MODEL])
    assert timedelta(minutes=59) < until - datetime.now(timezone.utc) <= timedelta(hours=1)
    # Make B busier so a run-count ordering alone would choose A again.
    state["accounts"]["account-b"]["runs"] *= 3
    (seats / "state.json").write_text(json.dumps(state))
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert calls[-1]["account"] == "account-b"
    state = json.loads((seats / "state.json").read_text())
    state["accounts"]["account-a"]["model_unavailable"][MODEL] = "2000-01-01T00:00:00Z"
    (seats / "state.json").write_text(json.dumps(state))
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert calls[-1]["account"] == "account-a"


def test_all_accounts_missing_model_returns_no_account_envelope(dispatch):
    run, _ = dispatch
    result, calls = run("both_missing")
    assert result.returncode == 2, result.stderr
    assert [row["account"] for row in calls] == ["account-a", "account-b"]
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert "Cannot use this model" in envelope["error"]
    assert all(attempt["outcome"] == "model_unavailable" for attempt in envelope["attempts"])


@pytest.mark.parametrize("error", ["Error: [unavailable] connect ETIMEDOUT 127.0.0.1:2458",
                                   "Failed to reach the Cursor API. Check that your proxy"])
def test_transient_infra_retries_same_account(dispatch, error):
    run, _ = dispatch
    result, calls = run("transient", TRANSIENT_ERROR=error)
    assert result.returncode == 0, result.stderr
    assert [row["account"] for row in calls] == ["account-a", "account-a"]
    assert [a["outcome"] for a in json.loads(result.stdout)["attempts"]] == ["transient_infra", "ok"]


def test_transient_infra_exhausts_accounts_with_no_account_envelope(dispatch):
    run, _ = dispatch
    result, calls = run("infra_exhausted")
    assert result.returncode == 2, result.stderr
    assert [row["account"] for row in calls] == ["account-a", "account-a", "account-b", "account-b"]
    assert "Failed to reach the Cursor API" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("text, expected", [
    ("connect ETIMEDOUT 127.0.0.1:2458\nusage limit reached", "limit"),
    ('The prompt quotes "Cannot use this model: X"', "error"),
    ("connect ETIMEDOUT 127.0.0.1:2458", "error"),
])
def test_release_transcript_does_not_park_model(dispatch, text, expected):
    run, seats = dispatch
    result, _ = run(argv=["lease", "--vendor", "cursor", "--for", "fixture",
                          "--ttl", "60", "--out", str(seats / "leased"), "--json"])
    assert result.returncode == 0, result.stderr
    lease = json.loads(result.stdout)
    log = seats / "run.log"
    log.write_text(text)
    result, _ = run(argv=["release", lease["lease"], "--log", str(log),
                          "--model", MODEL, "--json"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome"] == expected
    record = json.loads((seats / "state.json").read_text())["accounts"][lease["account"]]
    assert not record.get("model_unavailable")
    if expected == "limit":
        assert record.get("cooldown_until") or record.get("cooldowns")
    else:
        assert not record.get("cooldown_until") and not record.get("cooldowns")


def test_parked_envelope_accounts_and_probe_recovery(dispatch):
    run, seats = dispatch
    result, calls = run("both_missing")
    assert result.returncode == 2, result.stderr
    first = json.loads(result.stdout)
    state = json.loads((seats / "state.json").read_text())
    for record in state["accounts"].values():
        record["last_error"] = {"text": "unrelated later error"}
    (seats / "state.json").write_text(json.dumps(state))
    result, parked_calls = run()
    assert result.returncode == 2, result.stderr
    parked = json.loads(result.stdout)
    assert parked.keys() == first.keys()
    assert parked["error"] == first["error"]
    assert parked["attempts"] == []
    assert parked["wall_s"] == 0
    assert parked_calls == calls
    result, _ = run(argv=["accounts", "--json"])
    assert result.returncode == 0, result.stderr
    accounts = json.loads(result.stdout)["accounts"]
    assert all(datetime.fromisoformat(a["model_unavailable"][MODEL]) > datetime.now(timezone.utc)
               for a in accounts)
    result, _ = run(argv=["accounts"])
    assert result.returncode == 0, result.stderr
    assert MODEL in result.stdout
    assert all(a["model_unavailable"][MODEL] in result.stdout for a in accounts)
    result, _ = run(argv=["probe", "account-a", "--json"])
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == {"account-a"}
    result, _ = run(argv=["accounts", "--json"])
    accounts = {a["id"]: a for a in json.loads(result.stdout)["accounts"]}
    assert accounts["account-a"]["model_unavailable"] == {}
    assert MODEL in accounts["account-b"]["model_unavailable"]
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert calls[-1]["account"] == "account-a"
