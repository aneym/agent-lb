"""Accounting contracts: independent arithmetic, attribution overrides and waste boundaries.

These are new CLI behaviors, not covered by request-path pricing tests. Fixtures
exercise double-counting, imputation provenance and false session starts without
DB mocks or test-only production hooks.
"""

import json
import tomllib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.audit_tokens import (
    RULE_PATH,
    allocate,
    build_report,
    cache_kind,
    classify,
    price_row,
    prompt_text,
    quota_allocation,
    session_agents,
    token_classes,
    transcript_digest,
    waste_flags,
)


def receipt(**changes):
    row = dict(
        provider="openai",
        model="gpt-6.1-sol",
        input_tokens=1000,
        cached_input_tokens=800,
        output_tokens=100,
        reasoning_tokens=50,
        cost_usd=None,
        sid="session",
        account_id="abcdefgh-full",
        requests=1,
        reasoning_effort=None,
        useragent_group="codex_exec",
        status="success",
    )
    return row | changes


def test_token_classes_do_not_double_count_cached_or_reasoning():
    assert token_classes(receipt()) == dict(fresh_input=200, cache_read=800, cache_write=0, output=100, reasoning=50)
    assert token_classes(receipt(cached_input_tokens=2000))["fresh_input"] == 0
    assert token_classes(receipt(provider="anthropic", cache_creation_tokens=300, cache_read_tokens=500)) == dict(
        fresh_input=1000, cache_read=500, cache_write=300, output=100, reasoning=50
    )


def test_prices_observed_recomputed_and_unknown():
    usd, provenance, split = price_row(receipt())
    assert usd == pytest.approx(0.00148)
    assert provenance == "recomputed"
    assert sum(split.values()) == pytest.approx(usd)
    usd, provenance, split = price_row(receipt(cost_usd=0.02))
    assert usd == 0.02 and provenance == "log"
    assert sum(split.values()) == pytest.approx(0.02)
    assert price_row(receipt(model="unrecognized"))[:2] == (0, "unpriced")
    assert price_row(receipt(input_tokens=None))[:2] == (0, "unpriced")
    assert price_row(receipt(provider="kimi"))[:2] == (0, "unpriced")


def test_prompt_unwrap_and_rule_order():
    with RULE_PATH.open("rb") as stream:
        rules = tomllib.load(stream)["rules"]
    text = prompt_text(
        '<system-reminder>ignore</system-reminder><pasted_content id="x">scope this PRD</pasted_content>'
    )
    assert text == "scope this PRD"
    assert prompt_text([{"type": "tool_result", "content": "noise"}]) == ""
    assert prompt_text("<command-name>help</command-name>") == ""
    meta = dict(prompt="You are the bugs lane, reporting to the orchestrator", entrypoint="cli")
    assert classify(receipt(), meta, rules)[0] == "orchestrator"
    assert classify(receipt(useragent_group="parallel-load-eval"), meta, rules)[0] == "eval"
    assert classify(receipt(), {"prompt": "Please help debug", "entrypoint": "cli"}, rules)[0] == "ad-hoc"
    assert classify(receipt(), {"prompt": "Verify a GPT contract", "entrypoint": "sdk-cli"}, rules)[0] == "fold-verify"
    remote = receipt(useragent_group="codex_chatgpt_ios_remote")
    assert classify(remote, {"cwd": "/work/orch-lab", "entrypoint": "cli"}, rules)[0] == "eval"
    assert classify(remote, {"title": "[scoping] bridge audit", "entrypoint": "cli"}, rules)[0] == "scoping"


def test_codex_rollout_lanes_use_distinct_session_uuids(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    labels = []
    for uuid in ("0199abcd-1234-5678-9abc-123456789abc", "0199abcd-5345-5678-9abc-1234abcdef12"):
        path = tmp_path / f"rollout-2026-09-29T10-00-00-{uuid}.jsonl"
        path.write_text('{}\n')
        meta = transcript_digest(path)
        labels.append(classify({"sid": uuid, "useragent_group": "Codex"}, meta, [])[1])
    assert labels == ["Codex 56789abc", "Codex abcdef12"]
    assert len(set(labels)) == 2
    assert all("rollout" not in label for label in labels)


def test_lane_fallback_uses_client_session_without_prompt_text():
    row = {"useragent_group": "Claude", "client_session_id": "6f020060-rest"}
    assert classify(row, {"entrypoint": "cli"}, [])[1] == "Claude 6f020060"


def test_exclusive_cache_premiums_and_retry_boundaries():
    prior = dict(
        timestamp="2026-09-29T10:00:00Z",
        input_tokens=0,
        cache_creation_tokens=100000,
        cache_read_tokens=0,
        account_id="one",
    )
    event = prior | {"timestamp": "2026-09-29T10:01:00Z", "account_id": "two"}
    assert cache_kind(event, None) == "first_write"
    assert cache_kind(event, prior) == "account_switch"
    assert cache_kind(event | {"timestamp": "2026-09-29T10:06:00Z"}, prior) == "ttl_expiry"
    assert cache_kind(event | {"account_id": "one"}, prior) == "bust"
    assert cache_kind(event | {"account_id": "one", "cache_read_tokens": 95000}, prior) == "growth"
    assert waste_flags(receipt(request_id="r", retry_index=1)) == []
    assert waste_flags(receipt(request_id="r", retry_index=2, status="error", upstream_status_code=429)) == ["retried"]
    assert waste_flags(receipt(request_id=None, retry_index=2)) == []
    rows = [
        receipt(
            provider="anthropic",
            model="claude-opus-5-5",
            input_tokens=0,
            cache_creation_tokens=100000,
            cost_usd=1,
            requested_at=__import__("datetime").datetime(2026, 9, 29, 10, i),
        )
        for i in (0, 1)
    ]
    report = build_report(rows, {}, {}, {}, [], ["purpose"], 10)
    assert report["waste"]["first_write"]["requests"] == 1
    assert report["waste"]["bust"]["premium_usd"] == pytest.approx(0.48)
    assert report["waste_total_usd"] == pytest.approx(0.48)


def test_agent_allocation_reconciles_each_lb_class():
    value = dict(
        requests=3,
        tokens=17,
        usd=0.13,
        token_classes={"fresh_input": 11, "cache_read": 6},
        usd_classes={"fresh_input": 0.1, "cache_read": 0.03},
    )
    agents = [
        dict(key="lead", seat="lead", kind="lead", weight=1),
        dict(key="subagent:builder", seat="builder", kind="subagent", weight=2),
    ]
    pieces = allocate(value, agents)
    builder = next(piece for agent, piece in pieces if agent["seat"] == "builder")
    assert builder["usd"] == pytest.approx(0.13 * 2 / 3)
    assert sum(piece["usd"] for _, piece in pieces) == value["usd"]
    assert sum(piece["token_classes"]["fresh_input"] for _, piece in pieces) == 11
    assert allocate(value, [agents[0] | {"weight": 0}])[0][1] == value


@pytest.mark.parametrize(
    "subagent_models,providers",
    [
        ([("gpt-implementer", "gpt-6-sol")], ["openai"]),
        ([("verifier", "claude-opus-5-5")], ["openai"]),
        ([("verifier", "claude-opus-5-5"), ("gpt-implementer", "gpt-6-sol")], ["anthropic", "openai"]),
    ],
)
def test_bridge_report_allocates_only_to_matching_provider_agents(tmp_path, monkeypatch, subagent_models, providers):
    monkeypatch.setenv("HOME", str(tmp_path))
    start = datetime(2026, 9, 22, tzinfo=UTC)
    session = tmp_path / ".claude" / "projects" / "session.jsonl"
    subagents = session.parent / session.stem / "subagents"
    subagents.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": start.isoformat(),
                "message": {
                    "id": "lead",
                    "model": "claude-opus-5-5",
                    "usage": {"input_tokens": 1000, "output_tokens": 100},
                },
            }
        ) + "\n"
    )
    for seat, model in subagent_models:
        child = subagents / f"agent-{seat}.jsonl"
        child.with_suffix(".meta.json").write_text(json.dumps({"agentType": seat}))
        child.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": start.isoformat(),
                    "message": {
                        "id": seat,
                        "model": model,
                        "usage": {"input_tokens": 1000, "output_tokens": 100},
                    },
                }
            ) + "\n"
        )
    agents = session_agents(session, start, start + timedelta(hours=1))
    rows = [
        receipt(
            provider=provider,
            model="claude-opus-5-5" if provider == "anthropic" else "gpt-6-sol",
            useragent_group="claude-cli",
            cost_usd=1,
            quota_points=10,
            quota_5h_points=2,
            requested_at=start,
        )
        for provider in providers
    ]
    report = build_report(rows, {}, {}, {}, [], ["seat", "provider"], 10, agents_by_session={"session": agents})
    for provider in providers:
        allocated = [value for value in report["sessions"] if value["provider"] == provider]
        for metric in ("requests", "tokens", "usd", "quota_points", "quota_5h_points"):
            provider_total = next(value for value in report["by"]["provider"] if value["name"] == provider)
            assert sum(value[metric] for value in allocated) == provider_total[metric]
        gpt = next((value for value in allocated if value["agent"] == "subagent:gpt-implementer"), {})
        if provider == "openai" and any(model.startswith("gpt-") for _, model in subagent_models):
            assert gpt["usd"] == 1
            assert gpt["quota_points"] == 10
            assert gpt["quota_5h_points"] == 2
            assert sum(value["usd"] for value in allocated if value["agent"] == "lead") == 0
        elif provider == "openai":
            assert len(allocated) == 1
            assert allocated[0]["agent"] == "lead"
            assert allocated[0]["seat"] == "lead"
            assert allocated[0]["usd"] == 1
        else:
            assert gpt.get("usd", 0) == 0
            assert gpt.get("quota_points", 0) == 0
            assert {value["seat"]: value["usd"] for value in allocated} == {"lead": 0.5, "verifier": 0.5}


def test_report_reconciles_aggregate_prices_and_token_totals():
    rows = [receipt(requests=2, cost_usd=0.04), receipt(sid="other", requests=3)]
    report = build_report(rows, {}, {}, {}, [], ["purpose", "provider"], 10)
    assert report["totals"]["requests"] == 5
    assert report["totals"]["tokens"] == 5500
    assert report["totals"]["usd"] == pytest.approx(0.04444)
    assert sum(report["totals"]["usd_classes"].values()) == pytest.approx(0.04444)
    assert report["priced_by"] == {"log": 2, "recomputed": 3}
    assert report["waste"]["unattributed"]["share"] == 1
    assert report["account_shares"][0]["usd_share"] == 1


@pytest.mark.parametrize(
    "meta,label",
    [
        ({"pane": "w5H:p11Y"}, "w5H:p11Y"),
        ({"pane": "w5H:p11Y", "pane_title": "Token audit"}, "w5H:p11Y Token audit"),
        ({"pane": "w5H:p11Y", "pane_title": "x" * 200}, "w5H:p11Y " + "x" * 151),
        ({"pane_title": "Token audit"}, "unknown"),
        ({"pane": "w5H:p11Y", "pane_title": "private@example.invalid"}, "unknown"),
    ],
)
def test_report_pane_attribution_reconciles_costs(meta, label):
    """Pure receipt attribution arithmetic covers missing, bounded and private labels."""
    rows = [receipt(cost_usd=0.04), receipt(sid="other", cost_usd=0.02)]
    report = build_report(rows, {}, {}, {"session": meta}, [], ["pane"], 10)
    expected = {label: 0.04}
    expected["unknown"] = expected.get("unknown", 0) + 0.02
    assert {item["name"]: item["usd"] for item in report["by"]["pane"]} == pytest.approx(expected)
    assert sum(item["usd"] for item in report["by"]["pane"]) == pytest.approx(0.06)


@pytest.mark.parametrize("dimensions", ["pane", "provider,pane"])
def test_pane_dimension_passes_cli_validation(monkeypatch, capsys, dimensions):
    from app import audit_tokens

    monkeypatch.setattr(audit_tokens, "pane_maps", lambda: ({}, []))
    audit_tokens.run(
        SimpleNamespace(
            window="24h", since=None, until=None, by=dimensions, top=10, snapshot_panes_only=True
        )
    )
    assert capsys.readouterr().out == "Pane snapshot complete. \n"


def test_quota_reset_jitter_allocation_and_empty_interval():
    start = datetime(2026, 9, 22, tzinfo=UTC)
    reset = (start + timedelta(days=7)).timestamp()
    snapshots = [
        dict(
            provider="anthropic",
            account_id="abcdefgh-full",
            window="secondary",
            window_minutes=10080,
            recorded_at=start + timedelta(hours=i),
            used_percent=used,
            reset_at=reset + (7 * 86400 if i >= 4 else 0),
        )
        for i, used in enumerate((10, 20, 19, 25, 3, 8))
    ]
    rows = [
        receipt(provider="anthropic", cost_usd=usd, requested_at=start + timedelta(minutes=minute))
        for minute, usd in ((30, 1), (45, 3), (150, 1), (210, 1))
    ]
    allocated, quota = quota_allocation(rows, snapshots, start, start + timedelta(hours=5))
    assert [v["weekly"] for v in allocated] == [2.5, 7.5, 5, 3]
    assert sum(v["weekly"] for v in allocated) == 18
    assert sum(v["points"] for v in quota["unattributed"]) == 5
    assert quota["accounts"][0]["quota_points"] == 23
    assert quota["providers"]["anthropic:weekly"]["quota_points"] == 23


def test_quota_pre_reset_rise_uses_window_baseline_not_old_period_peak():
    start = datetime(2026, 9, 22, tzinfo=UTC)
    reset = (start + timedelta(hours=3)).timestamp()
    snapshots = [
        dict(
            provider="anthropic",
            account_id="abcdefgh-full",
            window="secondary",
            window_minutes=10080,
            recorded_at=start + timedelta(hours=hour),
            used_percent=used,
            reset_at=reset + (7 * 86400 if hour >= 3 else 0),
        )
        for hour, used in ((-2, 100), (-1, 3), (2, 83), (3, 0), (4, 100))
    ]
    _, quota = quota_allocation([], snapshots, start, start + timedelta(hours=5))
    assert quota["accounts"][0]["quota_points"] == 180
    assert sum(value["points"] for value in quota["unattributed"]) == 180
    assert quota["providers"]["anthropic:weekly"]["account_weeks"] == 1.8


def test_credit_reset_superset_monotonicity_and_jitter():
    start = datetime(2026, 9, 22, tzinfo=UTC)
    reset = (start + timedelta(days=7)).timestamp()
    snapshots = [
        dict(
            provider="anthropic",
            account_id="abcdefgh-full",
            window="secondary",
            window_minutes=10080,
            recorded_at=start + timedelta(hours=hour),
            used_percent=used,
            reset_at=reset,
        )
        for hour, used in ((-2, 97), (-1, 100), (0, 97), (1, 100), (2, 0), (3, 80), (4, 100), (5, 97), (6, 100))
    ]
    _, full = quota_allocation([], snapshots, start - timedelta(hours=3), start + timedelta(hours=7))
    _, subset = quota_allocation([], snapshots, start, start + timedelta(hours=7))
    assert full["accounts"][0]["quota_points"] == 103
    assert subset["accounts"][0]["quota_points"] == 100
    assert full["accounts"][0]["quota_points"] >= subset["accounts"][0]["quota_points"]
    assert sum(item["points"] for item in subset["unattributed"]) == 100
