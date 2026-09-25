from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
ROUTING = ROOT / "config" / "coding-agents" / "ROUTING.md"
ADAPTER = ROOT / "config" / "coding-agents" / "claude-adapter.md"
VERIFIER = ROOT / "config" / "coding-agents" / "verify-routing"


def test_lineup_plans_on_opus_and_retires_fable_astra_and_gpt_5_6() -> None:
    routing = " ".join(ROUTING.read_text().split())
    adapter = " ".join(ADAPTER.read_text().split())
    verifier = VERIFIER.read_text()
    table = json.loads((ROOT / "config" / "coding-agents" / "routing-table.json").read_text())

    assert "## The lineup (owner, 2026-09-22)" in routing
    assert "## Operating rules (owner, 2026-09-22)" in routing
    for text in (routing, adapter):
        assert "Fable" in text and "Astra" in text and "gpt-5.6" in text
        assert "route resolve" in text
    assert "Fable drives" not in adapter
    for pattern in ("claude-fable-*", "gpt-*-astra", "gpt-5.6*"):
        assert pattern in table["retired"]
    for name in ("planner", "plan-reviewer", "frontend-designer", "verifier", "opus-seat"):
        definition = (ROOT / "config" / "coding-agents" / "agents" / f"{name}.md").read_text()
        assert "\nmodel: opus\n" in definition
    assert not (ROOT / "config" / "coding-agents" / "agents" / "astra.md").exists()
    assert 'settings.get("model") == "opus"' in verifier


def test_implementation_is_cheap_and_audited_by_the_other_vendor() -> None:
    agents = ROOT / "config" / "coding-agents" / "agents"
    table = json.loads((ROOT / "config" / "coding-agents" / "routing-table.json").read_text())

    assert not (agents / "implementer.md").exists()
    for name in ("codex-verifier", "codex-test-runner", "computer-use", "codex-sol"):
        assert 'route resolve sol-latest)"' in (agents / f"{name}.md").read_text()
    chain = table["classes"]["implement"]["chain"]
    assert [(entry["seat"], entry["model"]) for entry in chain] == [
        ("gpt-implementer", "sol-latest"),
        ("sonnet-implementer", "sonnet-latest"),
        ("opus-seat", "opus-latest"),
    ]
    assert [(entry["seat"], entry["model"]) for entry in table["classes"]["mechanical"]["chain"]] == [
        ("luna-implementer", "luna-latest"),
        ("cursor-seat", "grok-latest"),
        ("devin-seat", "swe-latest"),
    ]
    assert chain[-1]["min_pace"] == table["policy"]["pace"]["behind_lt"]
    audit = table["classes"]["implement"]["audit"]["by_author_vendor"]
    assert audit["anthropic"]["model"] == "sol-latest"
    assert {audit[vendor]["model"] for vendor in ("openai", "cursor", "glm", "kimi")} == {"opus-latest"}


def test_fable_telemetry_and_historical_fixtures_are_not_route_migrated() -> None:
    launcher = (ROOT / "clients" / "claude-lb-launch").read_text()
    pricing = (ROOT / "app" / "core" / "anthropic" / "pricing.py").read_text()
    pulse_test = (ROOT / "tests" / "unit" / "test_account_pulse.py").read_text()
    fixture_path = ROOT / "clients" / "macos-menubar" / "Tests" / "AgentLBTests" / "Fixtures" / "request-logs.json"
    fixture = fixture_path.read_text()

    assert 'FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"' in launcher
    assert '"claude-fable-5": AnthropicModelPrice(' in pricing
    # The pulse probes the current Fable (5.1); the 5.0 price and fixtures stay.
    assert 'calls[0]["model"] == "claude-fable-5-1"' in pulse_test
    assert '"model":"claude-fable-5"' in fixture
