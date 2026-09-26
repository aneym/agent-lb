from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
def _source_env(home: Path) -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith("ROUTE_") and not key.startswith("SEAT_GUARD_")
        and key not in ("ROUTING_TABLE", "AGENT_LB_USER_HOME")
    } | {"HOME": str(home), "AGENT_LB_USER_HOME": str(home), "AGENT_LB_URL": "http://127.0.0.1:1"}


def _verify(source: Path, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(source / "verify-routing"), "--source-only"],
        env=_source_env(home), capture_output=True, text=True, timeout=60,
    )


def test_source_canon_passes_verify_routing(tmp_path: Path) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    result = _verify(ROOT / "config" / "coding-agents", home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(home.iterdir()) == []


@pytest.mark.parametrize(
    ("violation", "expected"),
    [
        ("implement-head", "factory:implement-head"),
        ("off-default", "factory:off-default"),
        ("audit-cross-vendor", "factory:audit-cross-vendor"),
        ("review-policy", "factory:review-policy"),
        ("implement-effort", "factory:stage-effort"),
        ("explore-effort", "factory:stage-effort"),
        ("forwarder-effort", "factory:stage-effort"),
        ("second-opinion", "factory:second-opinion"),
        ("undated-heading", "factory:routing-doc"),
        ("oversized-doc", "factory:routing-doc"),
        ("missing-definition", "factory:seat-definitions"),
        ("private-path", "factory:public-text"),
        ("retired", "retired"),
    ],
)
def test_verify_routing_rejects_each_factory_violation(
    tmp_path: Path, violation: str, expected: str,
) -> None:
    source = tmp_path / "coding-agents"
    shutil.copytree(ROOT / "config" / "coding-agents", source)
    table_path = source / "routing-table.json"
    table = json.loads(table_path.read_text())
    classes = table["classes"]
    if violation == "implement-head":
        classes["implement"]["chain"][0]["seat"] = "luna-implementer"
    elif violation == "off-default":
        classes["research"]["chain"].append(
            {"seat": "sonnet-implementer", "model": "sonnet-latest", "vendor": "anthropic", "effort": "high"}
        )
    elif violation == "audit-cross-vendor":
        classes["implement"]["audit"]["by_author_vendor"]["anthropic"] = {
            "seat": "verifier", "model": "opus-latest", "vendor": "anthropic", "effort": "high"
        }
    elif violation == "review-policy":
        table["policy"]["review"]["money_path"]["rule"] = "majority"
    elif violation == "implement-effort":
        classes["implement"]["chain"][0]["effort"] = "high"
    elif violation == "explore-effort":
        classes["explore"]["chain"][0]["effort"] = "xhigh"
    elif violation == "second-opinion":
        del classes["plan"]["second_opinion"]
    elif violation == "retired":
        classes["implement"]["chain"][0]["model"] = "gpt-5.6-sol"
    if violation in ("forwarder-effort", "undated-heading", "oversized-doc", "missing-definition", "private-path"):
        if violation == "forwarder-effort":
            path = source / "agents" / "codex-sol.md"
            path.write_text(path.read_text().replace("effort: low", "effort: high"))
        elif violation == "undated-heading":
            with (source / "ROUTING.md").open("a") as f:
                f.write("\n## Undated\n")
        elif violation == "oversized-doc":
            path = source / "ROUTING.md"
            path.write_text(path.read_text() + "\n" * (152 - len(path.read_text().splitlines())))
        elif violation == "missing-definition":
            (source / "agents" / "sol-consult.md").unlink()
        else:
            with (source / "agents" / "opus-seat.md").open("a") as f:
                f.write("\n/Users/someone/x\n")
    else:
        table_path.write_text(json.dumps(table))
    home = tmp_path / "empty-home"
    home.mkdir()
    result = _verify(source, home)
    assert result.returncode == 1, result.stdout + result.stderr
    if expected == "retired":
        assert "FAIL no retired or older-than-newest model is pinned" in result.stdout
        assert "gpt-5.6-sol (retired)" in result.stdout
    else:
        assert f"FAIL {expected} " in result.stdout


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
