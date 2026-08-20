from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
ROUTING = ROOT / "config" / "coding-agents" / "ROUTING.md"
ADAPTER = ROOT / "config" / "coding-agents" / "claude-adapter.md"
VERIFIER = ROOT / "config" / "coding-agents" / "verify-routing"


def test_canonical_claude_native_routes_use_opus_5_high_with_fable_first_planner() -> None:
    routing = ROUTING.read_text()
    adapter = ADAPTER.read_text()
    verifier = VERIFIER.read_text()

    assert "| Driver (main loop)" in routing
    assert "| `claude-opus-5`" in routing
    assert "| high" in routing
    assert "| Frontend designer" in routing
    assert "| Planner (lane lead)" in routing
    planner_row = next(line for line in routing.splitlines() if "| Planner (lane lead)" in line)
    assert "`claude-planner`" in planner_row
    assert "Fable 5 primary, Opus 5 on scoped exhaustion" in planner_row
    for legacy in ("claude-opus-4-8", "Fable/high"):
        assert legacy not in "\n".join(
            line
            for line in routing.splitlines()
            if any(route in line for route in ("Driver (main loop)", "Frontend designer"))
        )
        assert legacy not in adapter
    assert 'settings.get("model") == "fable"' in verifier
    assert 'settings.get("effortLevel") == "high"' in verifier
    assert "model: claude-planner" in verifier
    assert "claude-opus-5" in verifier


def test_gpt_sol_routes_remain_fixed() -> None:
    routing = ROUTING.read_text()
    verifier = VERIFIER.read_text()
    expected_rows = (
        "| Explore / scouts       | `~/.claude/agents/Explore.md`           "
        "| `gpt-5.6-sol-medium` | medium, fast tier |",
        "| Implementer            | `~/.claude/agents/implementer.md`       "
        "| `gpt-5.6-terra-medium` | medium, fast tier |",
        "| Verifier (adversarial) | `~/.claude/agents/verifier.md`          "
        "| `gpt-5.6-sol-xhigh`  | xhigh, fast tier  |",
    )
    for row in expected_rows:
        assert row in routing
    assert 'CCGPT_MODEL = "gpt-5.6-sol"' in verifier
    assert '"--effort", "high"' in verifier


def test_fable_telemetry_and_historical_fixtures_are_not_route_migrated() -> None:
    launcher = (ROOT / "clients" / "claude-lb-launch").read_text()
    pricing = (ROOT / "app" / "core" / "anthropic" / "pricing.py").read_text()
    pulse_test = (ROOT / "tests" / "unit" / "test_account_pulse.py").read_text()
    fixture_path = ROOT / "clients" / "macos-menubar" / "Tests" / "AgentLBTests" / "Fixtures" / "request-logs.json"
    fixture = fixture_path.read_text()

    assert 'FABLE_SCOPED_WEEKLY_QUOTA_KEY = "anthropic_fable_scoped_weekly"' in launcher
    assert '"claude-fable-5": AnthropicModelPrice(' in pricing
    assert 'calls[0]["model"] == "claude-fable-5"' in pulse_test
    assert '"model":"claude-fable-5"' in fixture
