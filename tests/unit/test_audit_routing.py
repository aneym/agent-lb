"""Routing contracts: harness ownership, all-stage accepted costs and sample guards."""

from datetime import UTC, datetime, timedelta

import pytest

from app.audit_routing import fold_units, harness, movements, routing_rows


def test_harness_classifies_bridge_and_native_clients():
    for client in ("claude-cli", "Claude"):
        assert harness(dict(provider="openai", useragent_group=client)) == "claude-code"
    for client in ("codex_exec", "codex-tui", "Codex", "codex_cli_rs"):
        assert harness(dict(provider="openai", useragent_group=client)) == "codex-cli"
    assert harness(dict(provider="anthropic", useragent_group="unknown")) == "claude-code"
    assert harness(dict(provider="openai", useragent_group="eval")) == "other"


def test_routing_allocates_only_matching_models_and_uses_agent_start():
    events = [
        dict(
            timestamp="2026-09-29T00:00:00Z",
            model="gpt-6.1-sol",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=1,
        )
    ]
    agents = {
        "s": [
            dict(key="workflow:wf_fixture:impl:A", seat="gpt-implementer", kind="workflow", events=events),
            dict(
                key="workflow:wf_fixture:verify:A",
                seat="verifier",
                kind="workflow",
                events=[events[0] | {"model": "claude-opus-5-5"}],
            ),
        ]
    }
    rows = [
        dict(
            sid="s",
            provider="openai",
            model="gpt-6.1-sol",
            useragent_group="Claude",
            input_tokens=1000,
            cached_input_tokens=800,
            output_tokens=20,
            quota_points=4,
        )
    ]
    result, points = routing_rows(rows, agents, {})
    active = [row for row in result if row["requests"] > 0]
    assert len(active) == 1
    assert active[0]["seat"] == "gpt-implementer"
    assert active[0]["median_start_tokens"] == 100
    assert active[0]["cache_hit_rate"] == 0.8
    assert points["workflow:wf_fixture:impl:A"]["openai"] == 4
    assert sum(row["quota_points"] for row in result) == 4


def test_fold_two_pieces_counts_failed_work_in_accepted_unit_cost():
    start = datetime(2026, 9, 29, tzinfo=UTC)
    agents = []
    points = {}
    for i, (phase, title) in enumerate(
        (("impl", "A"), ("verify", "A"), ("impl", "B"), ("verify", "B"), ("fix-r1", "B"), ("verify-r1", "B"))
    ):
        label = f"{phase}:{title}"
        agents.append(
            dict(
                type="workflow_agent",
                label=label,
                agentType="gpt-implementer",
                model="sol-latest-medium",
                startedAt=(start.timestamp() + i * 60) * 1000,
                durationMs=60000,
            )
        )
        points[f"workflow:wf_fixture:{label}"] = {"openai": 2, "anthropic": 1}
    record = dict(
        workflowName="fold-pipeline-v2",
        timestamp=start.isoformat(),
        workflowProgress=agents,
        result=dict(pieces=[dict(title="A", status="pass"), dict(title="B", status="FAIL")]),
    )
    (row,) = fold_units([("wf_fixture", record)], points, start, start + timedelta(hours=1))
    assert (row["pieces"], row["accepted"], row["fix_rounds"], row["review_rounds"]) == (2, 1, 1, 3)
    assert row["accept_rate"] == 0.5
    assert row["wall_minutes_per_accepted_piece"] == 6
    assert row["points_per_accepted_piece"] == {"openai": 12, "anthropic": 6}


def test_movement_requires_more_than_quarter_and_ten_samples_in_both_windows():
    base = dict(
        pool="openai",
        seat="builder",
        model="sol",
        effort="medium",
        harness="codex-cli",
        requests=10,
        points_per_request=1.0,
        cache_hit_rate=0.4,
    )
    before = dict(rows=[base], fold=[])

    def current(**changes):
        return dict(rows=[base | changes], fold=[])

    for previous in (0.0, 1e-10, -1e-10):
        previous_report = dict(rows=[base | {"points_per_request": previous}], fold=[])
        assert movements(current(points_per_request=1), previous_report) == []
    tiny_before = dict(rows=[base | {"points_per_request": 1e-10}], fold=[])
    assert movements(current(points_per_request=-1e-10), tiny_before) == []
    normalized = movements(current(points_per_request=-1e-10), before)
    assert normalized[0]["current"] == 0
    assert movements(current(points_per_request=1.25), before) == []
    changed = movements(current(points_per_request=1.26), before)
    assert changed[0]["previous"] == 1
    assert changed[0]["current"] == pytest.approx(1.26)
    assert changed[0]["current_n"] == changed[0]["previous_n"] == 10
    assert movements(current(requests=9, points_per_request=2), before) == []
    assert movements(current(points_per_request=2), dict(rows=[base | {"requests": 9}], fold=[])) == []
    assert movements(current(cache_hit_rate=0.6), before)[0]["metric"] == "cache_hit_rate"
    fold = dict(seat="builder", model="sol", pieces=10, accept_rate=0.8, points_per_accepted_piece={"openai": 2})
    result = movements(
        dict(rows=[], fold=[fold | {"points_per_accepted_piece": {"openai": 3}}]), dict(rows=[], fold=[fold])
    )
    assert result[0]["pool"] == "openai"
    assert result[0]["metric"] == "points_per_accepted_piece"
