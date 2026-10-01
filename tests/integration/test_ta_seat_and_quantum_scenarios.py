"""Token-audit follow-ups (pKE, 2026-10-01).

TA-04: 45.6 of 75 OpenAI points landed on harness labels (codex:codex_exec, codex:Claude,
codex:claude-cli) because a Codex request carries no seat. A launcher now names its seat in
AGENT_LB_SEAT; Codex sends it as x-agent-lb-seat (env_http_headers on the agent-lb provider),
the Claude launcher sends the same header, the request log stores it, and the audit labels the
row with it when no transcript names an agent.

TA-05: OpenAI weekly used_percent moves in whole 1% steps, so an hourly bin holds 0 or 1 point
and its holdout R² is mostly rounding noise (it read -0.63). Each binned fit now reports the
quota step, the holdout's predicted vs actual total, and flags bins too small to score; a 6 h
bin is added.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.audit_tokens import build_report, quota_allocation
from app.db.models import RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_the_seat_header_lands_on_the_request_log_row(async_client):
    cases = {
        "seat-plain": ({"x-agent-lb-seat": "gpt-implementer"}, "gpt-implementer"),
        "seat-normalized": ({"x-agent-lb-seat": "  Codex-Verifier "}, "codex-verifier"),
        "seat-with-model": ({"x-agent-lb-seat": "cursor-seat@grok-latest"}, "cursor-seat@grok-latest"),
        "seat-garbage": ({"x-agent-lb-seat": "bad seat; rm -rf"}, None),
        "seat-missing": ({}, None),
    }
    for request_id, (headers, _) in cases.items():
        response = await async_client.post(
            "/backend-api/codex/responses",
            json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
            headers={"x-request-id": request_id, **headers},
        )
        assert response.status_code == 200
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).where(RequestLog.request_id.in_(list(cases))))
        rows = {row.request_id: row.caller_seat for row in result.scalars()}
    assert rows == {request_id: expected for request_id, (_, expected) in cases.items()}


def _receipt(**changes):
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
        quota_points=1,
        quota_5h_points=0,
        requested_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )
    return row | changes


def test_the_audit_names_the_seat_from_the_request_log():
    rows = [
        _receipt(sid="exec-1", useragent_group="codex_exec", caller_seat="gpt-implementer", quota_points=5),
        _receipt(sid="companion-1", useragent_group="Claude", caller_seat="codex-verifier", quota_points=3),
        _receipt(sid="bridge-1", useragent_group="claude-cli", caller_seat="sol-consult", quota_points=2),
        _receipt(sid="exec-2", useragent_group="codex_exec", caller_seat=None, quota_points=1),
    ]
    report = build_report(rows, {}, {}, {}, [], ["seat", "provider"], 10)
    seats = {value["name"]: value["quota_points"] for value in report["by"]["seat"]}
    assert seats["gpt-implementer"] == pytest.approx(5)
    assert seats["codex-verifier"] == pytest.approx(3)
    assert seats["sol-consult"] == pytest.approx(2)
    # A request with no seat header keeps the harness fallback.
    assert seats["codex:codex_exec"] == pytest.approx(1)


def test_codex_sends_the_launchers_seat_and_the_claude_launcher_tags_it(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "agent-lb"\n\n'
        "[model_providers.agent-lb]\n"
        'name = "Local Agent LB"\n'
        'base_url = "http://127.0.0.1:2455/backend-api/codex"\n'
        'wire_api = "responses"\n'
        "supports_websockets = true\n"
        "requires_openai_auth = true\n"
    )
    env = {
        **os.environ,
        "CODEX_ROUTING_GUARD_SKIP_HEALTH": "1",
        "CODEX_ROUTING_GUARD_LOG": str(tmp_path / "guard.log"),
    }
    command = [sys.executable, str(ROOT / "clients" / "codex-routing-guard"), "--config", str(config)]
    first = subprocess.run([*command, "--provider", "agent-lb"], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    provider = tomllib.loads(config.read_text())["model_providers"]["agent-lb"]
    assert provider["env_http_headers"] == {"x-agent-lb-seat": "AGENT_LB_SEAT"}
    settled = config.read_text()
    second = subprocess.run([*command, "--provider", "agent-lb"], env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert config.read_text() == settled

    loader = importlib.machinery.SourceFileLoader("claude_lb_launch_seat", str(ROOT / "clients" / "claude-lb-launch"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.tag_headers({"AGENT_LB_SEAT": "gpt-implementer"}) == {"x-agent-lb-seat": "gpt-implementer"}
    assert module.tag_headers({"AGENT_LB_SEAT": ""}) == {}


def _snapshot(stamp, used):
    return {
        "provider": "openai",
        "account_id": "account-a",
        "window": "primary",
        "window_minutes": 10080,
        "recorded_at": stamp,
        "used_percent": used,
        "reset_at": datetime(2026, 10, 1, tzinfo=UTC).timestamp(),
    }


def test_quantized_hourly_bins_report_total_error_and_a_six_hour_fit():
    # True burn 0.8 points per hour; the snapshot shows whole percent steps.
    start = datetime(2026, 9, 22, tzinfo=UTC)
    rows, snapshots = [], [_snapshot(start - timedelta(minutes=1), 0)]
    for hour in range(48):
        stamp = start + timedelta(hours=hour, minutes=10)
        rows.append(
            {
                "provider": "openai",
                "account_id": "account-a",
                "model": "gpt-6.1-sol",
                "status": "success",
                "requested_at": stamp,
                "input_tokens": 1000,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0,
            }
        )
        snapshots.append(_snapshot(stamp + timedelta(minutes=20), int(0.8 * (hour + 1))))
    _, quota = quota_allocation(rows, snapshots, start, start + timedelta(days=2))
    fits = {
        fit["bin_hours"]: fit
        for fit in quota["calibration"]
        if fit.get("bin_hours") and fit["provider"] == "openai" and fit["window"] == "weekly"
    }
    assert set(fits) == {1, 3, 6}
    for fit in fits.values():
        assert fit["quantum_points"] == pytest.approx(1)
        assert fit["holdout_points"] == pytest.approx(19)
        assert fit["holdout_predicted_points"] == pytest.approx(19, rel=0.15)
        assert abs(fit["holdout_total_error"]) <= 0.15
    assert fits[1]["quantized"] is True and fits[3]["quantized"] is True
    assert "holdout_total_error" in fits[1]["note"]
    assert fits[6]["quantized"] is False
    assert fits[6]["holdout_points_per_bin"] >= 3
