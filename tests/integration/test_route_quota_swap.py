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


@pytest.mark.parametrize("alias", ["sonnet-latest", "opus-latest"])
def test_explicit_alias_keeps_anthropic_during_quota_swap(tmp_path, alias):
    env = environment(tmp_path)
    pool(env, 12)
    assert route(env, "quota-mode")["mode"] == "codex-swap"
    assert route(env, "resolve", alias)["model"].startswith("claude-")
    assert route(env, "resolve", "review")["model"] == "gpt-6.1-sol"


@pytest.mark.parametrize("eligible,status,source,degraded", [
    (0, "rate_limited", "live", True),
    (0, "quota_exceeded", "live", True),
    (1, "rate_limited", "live", False),
    (0, "active", "live", False),
    (0, "quota_exceeded", "accounts_fallback", False),
])
def test_explicit_alias_degrades_only_on_live_exhaustion(tmp_path, eligible, status, source, degraded):
    env = environment(tmp_path)
    fixtures = Path(env["ROUTE_FIXTURE_DIR"])
    path = fixtures / "api_pools.json"
    document = json.loads(path.read_text())
    document["pools"][1].update(eligibleAccounts=eligible, accounts=1, source=source)
    path.write_text(json.dumps(document))
    (fixtures / "api_accounts.json").write_text(json.dumps({"accounts": [
        {"provider": "anthropic", "status": status}]}))
    result = route(env, "resolve", "sonnet-latest")
    assert (result["model"] == "gpt-6.1-sol") is degraded
    if degraded:
        events = [json.loads(line) for line in Path(env["ROUTE_LEDGER"]).read_text().splitlines()]
        assert events[-1]["event"] == "model_degraded"


@pytest.mark.parametrize("document", [None, [], {}, {"pools": None}, {"pools": {}},
                                     {"pools": [None, 1, {}, {"id": []}]}])
def test_malformed_pool_data_keeps_existing_swap(tmp_path, document):
    env = environment(tmp_path)
    pool(env, 12)
    assert route(env, "quota-mode")["mode"] == "codex-swap"
    (Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json").write_text(json.dumps(document))
    state = route(env, "quota-mode")
    assert state["mode"] == "codex-swap" and state["remaining_fraction"] is None


@pytest.mark.parametrize("state", [[], None, "anthropic", 1, True])
def test_non_object_mode_state_recovers_for_dispatch_and_resolve(tmp_path, state):
    env = environment(tmp_path)
    path = Path(env["ROUTE_QUOTA_MODE_STATE"])
    for command in (("dispatch", "verdict", "--job", "state-recovery"), ("resolve", "verdict")):
        path.write_text(json.dumps(state))
        assert route(env, *command)["model"].startswith("claude-")
        assert json.loads(path.read_text())["mode"] == "anthropic"


@pytest.mark.parametrize("field,low,hold,recovered", [
    ("eligibleAccounts", 1, 1, 2),
    ("headroomPercent", 39, 50, 51),
])
def test_reserve_conditions_swap_authority_classes_and_hold_until_recovery(tmp_path, field, low, hold, recovered):
    env = environment(tmp_path)
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    for value, expected in ((low, "gpt-6.1-sol"), (hold, "gpt-6.1-sol"), (recovered, "claude-opus-5-5")):
        document["pools"][1][field] = value
        path.write_text(json.dumps(document))
        assert route(env, "resolve", "verdict")["model"] == expected
        assert route(env, "dispatch", "verdict", "--job", f"reserve-{value}")["model"] == expected


@pytest.mark.parametrize("value", ["broken", None, [], True, "NaN", "Infinity"])
def test_malformed_reserve_policy_uses_default_and_logs(tmp_path, value):
    env = environment(tmp_path)
    path = Path(env["ROUTE_TABLE"])
    document = json.loads(path.read_text())
    document["policy"]["reserve"]["headroom_min_percent"] = value
    path.write_text(json.dumps(document))
    pool(env, 14)
    state = route(env, "quota-mode")
    assert state["mode"] == "codex-swap" and state["reserve_fraction"] == .15
    events = [json.loads(line) for line in Path(env["ROUTE_LEDGER"]).read_text().splitlines()]
    assert any(event["event"] == "route_quota_policy_invalid" for event in events)
