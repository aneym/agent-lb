import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.integration.route_support import SCRIPT, setup, table_copy


def route(env, *args):
    result = subprocess.run([sys.executable, str(SCRIPT), *args, "--json"], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def environment(tmp_path):
    env = setup(tmp_path, codex_low=False)
    env["ROUTE_TABLE"] = str(table_copy(tmp_path, "policy", lambda table: None))
    env["ROUTE_QUOTA_MODE_STATE"] = str(tmp_path / "quota.json")
    env["ROUTE_LEDGER"] = str(tmp_path / "ledger.jsonl")
    env["ROUTE_DECISIONS_LOG"] = str(tmp_path / "decisions.jsonl")
    fixtures = Path(env["ROUTE_FIXTURE_DIR"])
    (fixtures / "api_request-logs.json").write_text('{"requests": []}')
    (fixtures / "api_models.json").write_text(json.dumps({"models": [
        {"id": "gpt-6.1-sol"}, {"id": "gpt-6.1-astra"}]}))
    return env


def pool(env, remaining):
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    document["pools"][1]["aggregateRemainingPercent"] = remaining
    path.write_text(json.dumps(document))


def error(env, minutes=0, model="claude-opus-5-5"):
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_request-logs.json"
    path.write_text(json.dumps({"requests": [{"model": model, "upstreamStatusCode": 429,
        "requestedAt": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()}]}))


def test_threshold_hysteresis_recovery_and_events(tmp_path):
    env = environment(tmp_path)
    assert route(env, "quota-mode")["mode"] == "anthropic"
    pool(env, 39)
    down = route(env, "quota-mode")
    assert down["mode"] == "codex-swap" and down["remaining_fraction"] == .39
    pool(env, 45)
    assert route(env, "quota-mode")["last_flip_at"] == down["last_flip_at"]
    pool(env, 50)
    assert route(env, "quota-mode")["mode"] == "codex-swap"
    pool(env, 51)
    assert route(env, "quota-mode")["mode"] == "anthropic"
    events = [json.loads(line) for line in Path(env["ROUTE_LEDGER"]).read_text().splitlines()]
    assert [event["direction"] for event in events] == ["codex-swap", "anthropic"]
    assert all(event["cause"] and event["remaining_fraction"] for event in events)


def test_limit_error_and_fifteen_minute_cooldown(tmp_path):
    env = environment(tmp_path)
    error(env, 9)
    assert route(env, "quota-mode")["mode"] == "codex-swap"
    error(env, 12)
    path = Path(env["ROUTE_QUOTA_MODE_STATE"])
    state = json.loads(path.read_text())
    state["last_limit_error_at"] = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
    path.write_text(json.dumps(state))
    assert route(env, "quota-mode")["mode"] == "codex-swap"
    error(env, 16)
    state["last_limit_error_at"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    path.write_text(json.dumps(state))
    assert route(env, "quota-mode")["mode"] == "anthropic"


def test_old_and_other_vendor_errors_do_not_trip(tmp_path):
    env = environment(tmp_path)
    error(env, 11)
    assert route(env, "quota-mode")["mode"] == "anthropic"
    error(env, model="gpt-6.1-sol")
    assert route(env, "quota-mode")["mode"] == "anthropic"


@pytest.mark.parametrize("task,seat,model", [
    ("review", "sol-consult", "sol-latest"), ("judge", "sol-consult", "sol-latest"),
    ("audit", "sol-consult", "sol-latest"), ("verdict", "sol-consult", "sol-latest"),
    ("verify", "codex-verifier", "sol-latest"), ("plan", "astra-consult", "astra-latest"),
    ("scope", "astra-consult", "astra-latest"), ("spec", "astra-consult", "astra-latest"),
    ("contract", "astra-consult", "astra-latest"), ("architecture", "astra-consult", "astra-latest"),
    ("design", "astra-consult", "astra-latest"),
])
def test_dispatch_and_resolve_swapped_classes(tmp_path, task, seat, model):
    env = environment(tmp_path)
    pool(env, 10)
    result = route(env, "dispatch", task, "--job", "swap-job")
    assert (result["seat"], result["model_alias"], result["effort"]) == (seat, model, "high")
    resolved = route(env, "resolve", task)
    assert resolved["model"] == result["model"] and resolved["seat"] == seat


def test_first_arm_survives_both_flips_and_explicit_flags_win(tmp_path):
    env = environment(tmp_path)
    first = route(env, "dispatch", "review", "--job", "original")
    pool(env, 10)
    held = route(env, "dispatch", "review", "--job", "original")
    assert held["sticky"] and held["model_alias"] == first["model_alias"] == "opus-latest"
    swapped = route(env, "dispatch", "review", "--job", "swapped")
    explicit = route(env, "dispatch", "review", "--job", "explicit", "--model", "opus-latest", "--effort", "low")
    assert explicit["model_alias"] == "opus-latest" and explicit["effort"] == "low"
    same_vendor = route(env, "dispatch", "review", "--job", "diff", "--author-vendor", "openai")
    assert same_vendor["seat"] == "codex-verifier" and same_vendor["model_alias"] == "sol-latest"
    pool(env, 60)
    held = route(env, "dispatch", "review", "--job", "swapped")
    assert held["sticky"] and held["model_alias"] == swapped["model_alias"] == "sol-latest"
    assert route(env, "resolve", "opus-latest")["model"].startswith("claude-")


def test_concurrent_status_writes_only_one_flip(tmp_path):
    env = environment(tmp_path)
    pool(env, 10)
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(lambda _: route(env, "quota-mode"), range(4)))
    assert all(row["mode"] == "codex-swap" for row in rows)
    assert len(Path(env["ROUTE_LEDGER"]).read_text().splitlines()) == 1


def test_implementation_and_exploration_remain_unchanged(tmp_path):
    env = environment(tmp_path)
    table = Path(env["ROUTE_TABLE"])
    policy = json.loads(table.read_text())
    policy["explore"]["enabled"] = False
    table.write_text(json.dumps(policy))
    before = {task: route(env, "dispatch", task, "--job", "before-" + task)
              for task in ("implement", "explore")}
    pool(env, 1)
    for task, original in before.items():
        after = route(env, "dispatch", task, "--job", "after-" + task)
        assert (after["seat"], after["model_alias"], after["effort"]) == (
            original["seat"], original["model_alias"], original["effort"])


def test_unknown_quota_does_not_recover_swap(tmp_path):
    env = environment(tmp_path)
    pool(env, 1)
    assert route(env, "quota-mode")["mode"] == "codex-swap"
    pool(env, None)
    state = route(env, "quota-mode")
    assert state["mode"] == "codex-swap" and state["remaining_fraction"] is None
