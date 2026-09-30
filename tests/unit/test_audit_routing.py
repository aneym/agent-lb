"""Routing contracts: harness ownership, all-stage accepted costs and sample guards."""

from datetime import UTC, datetime, timedelta

import pytest

from app.audit_routing import fold_units, harness, movements, routing_rows, text_report
from app.audit_tokens import quota_allocation


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


def test_routing_pool_totals_include_unattributed_quota():
    start = datetime(2026, 9, 29, tzinfo=UTC)
    rows, snapshots = [], []
    for pool in ("anthropic", "openai"):
        rows.append(
            dict(
                sid=pool,
                provider=pool,
                account_id=pool,
                model="unknown",
                input_tokens=100,
                requested_at=start + timedelta(minutes=30),
            )
        )
        windows = [("secondary" if pool == "anthropic" else "primary", 10080)]
        if pool == "anthropic":
            windows.append(("primary", 300))
        for window, minutes in windows:
            for hour, used in enumerate((0, 10, 15)):
                snapshots.append(
                    dict(
                        provider=pool,
                        account_id=pool,
                        window=window,
                        window_minutes=minutes,
                        recorded_at=start + timedelta(hours=hour),
                        used_percent=used,
                        reset_at=(start + timedelta(days=7)).timestamp(),
                    )
                )
    allocation, quota = quota_allocation(rows, snapshots, start, start + timedelta(hours=2))
    for row, points in zip(rows, allocation, strict=True):
        row.update(quota_points=points["weekly"], quota_5h_points=points["5h"])
    grouped, _ = routing_rows(rows, {}, {}, quota)
    for pool in ("anthropic", "openai"):
        pool_rows = [row for row in grouped if row["pool"] == pool]
        windows = [("weekly", "quota_points")]
        if pool == "anthropic":
            windows.append(("5h", "quota_5h_points"))
        for window, field in windows:
            assert sum(row[field] for row in pool_rows) == pytest.approx(
                quota["providers"][f"{pool}:{window}"]["quota_points"],
                abs=0.01,
            )
        (unattributed,) = [row for row in pool_rows if row["seat"] == "unattributed"]
        assert (unattributed["model"], unattributed["harness"]) == ("unknown", "other")
        assert unattributed["quota_points"] == 5
        assert unattributed["quota_5h_points"] == (5 if pool == "anthropic" else 0)
        assert unattributed["requests"] == unattributed["agents"] == 0


@pytest.mark.parametrize(
    "statuses, seat_rate",
    [
        (["pass", "pass", "infra_blocked", "FAIL"], 2 / 3),
        (["infra_blocked", "pipeline_error"], None),
        (["override", "pipeline_error", "split", "founder_call", "held"], 1 / 4),
    ],
)
def test_fold_seat_accept_rate_excludes_only_infra_faults(statuses, seat_rate):
    start = datetime(2026, 9, 29, tzinfo=UTC)
    record = dict(
        workflowName="fold-pipeline-v2",
        timestamp=start.isoformat(),
        workflowProgress=[
            dict(
                type="workflow_agent",
                label=f"impl:Piece {i}",
                agentType="gpt-implementer",
                model="sol-latest-medium",
                startedAt=start.timestamp() * 1000,
                durationMs=60000,
            )
            for i in range(len(statuses))
        ],
        result=dict(pieces=[dict(title=f"Piece {i}", status=status) for i, status in enumerate(statuses)]),
    )
    (row,) = fold_units([("wf_fixture", record)], {}, start, start + timedelta(hours=1))
    assert row["accept_rate"] == sum(s in ("pass", "override") for s in statuses) / len(statuses)
    if seat_rate is None:
        assert row["seat_accept_rate"] is None
    else:
        assert row["seat_accept_rate"] == pytest.approx(seat_rate)
    assert row["status_counts"] == {status.lower(): statuses.count(status) for status in statuses}
    rendered = text_report(dict(window=dict(since="start", until="end"), rows=[], fold=[row], notes=[]))
    assert "SEAT RATE" in rendered
    if statuses == ["pass", "pass", "infra_blocked", "FAIL"]:
        assert "50.0% | 66.7%" in rendered


@pytest.mark.parametrize(
    "wall, points, expected",
    [
        (
            52.161549751243776,
            {"openai": 0.2288198485928572, "anthropic": 0.26587479571518197},
            "52.2 | anthropic 0.27, openai 0.23",
        ),
        (None, {"anthropic": None}, "n/a | anthropic n/a"),
    ],
)
def test_text_report_formats_fold_costs_and_unattributed_rows(wall, points, expected):
    rows, _ = routing_rows(
        [], {}, {}, {"unattributed": [dict(provider="anthropic", window="weekly", points=5)]}
    )
    fold = dict(
        seat="builder",
        model="sol",
        pieces=4,
        accepted=2,
        accept_rate=0.5,
        seat_accept_rate=2 / 3,
        fix_rounds=1,
        review_rounds=3,
        wall_minutes_per_accepted_piece=wall,
        points_per_accepted_piece=points,
        provisional=True,
    )
    rendered = text_report(dict(window=dict(since="start", until="end"), rows=rows, fold=[fold], notes=[]))
    assert expected in rendered
    assert "{'" not in rendered
    assert "50.0% | 66.7%" in rendered
    assert "unattributed / unknown / unknown / other | 0 | 5.00" in rendered
    assert fold["wall_minutes_per_accepted_piece"] == wall
    assert fold["points_per_accepted_piece"] == points


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
