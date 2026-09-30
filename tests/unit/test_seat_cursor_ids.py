"""Cursor alias and dispatch contracts exercised at the CLI boundary."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def cli(tmp_path: Path):
    cursor = tmp_path / "cursor"
    cursor.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if 'models' in sys.argv or '--list-models' in sys.argv:\n"
        "    with open(os.environ['SPAWN'] + '.models', 'a') as calls: calls.write('call\\n')\n"
        "    source = 'KEY_MODELS' if os.environ.get('CURSOR_API_KEY') else 'MODELS'\n"
        "    print(os.environ.get(source, ''))\n"
        "    sys.exit(int(os.environ.get('MODELS_EXIT', '0')))\n"
        "Path(os.environ['SPAWN']).write_text('spawned')\n"
        "print(json.dumps({'is_error': False, 'result': 'done'}))\n"
    )
    cursor.chmod(0o755)
    seats = tmp_path / "seats"
    seats.mkdir()
    (seats / "accounts.json").write_text(json.dumps({
        "accounts": [{"id": "fixture", "vendor": "cursor", "auth": "login"}],
    }))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ROUTE_", "SEAT_", "AGENT_LB_", "CURSOR_"))}
    env.update(
        HOME=str(tmp_path), SEAT_HOME=str(seats), ROUTE_LEDGER=str(tmp_path / "ledger"),
        ROUTE_TABLE=str(REPO / "config/coding-agents/routing-table.json"),
        ROUTE_BIN=str(REPO / "clients/route"), SEAT_CURSOR_BIN=str(cursor),
        ROUTE_CURSOR_MODELS_CMD=f"{cursor} models", ROUTE_FIXTURE_DIR=str(tmp_path),
        SPAWN=str(tmp_path / "spawn"),
    )

    def run(script: str, *args: str, models: str = "", models_exit: int = 0,
            key_models: str = "", api_key_account: bool = False):
        if api_key_account:
            key_file = tmp_path / "fixture.key"
            key_file.write_text("fixture-not-a-credential")
            (seats / "accounts.json").write_text(json.dumps({"accounts": [
                {"id": "fixture", "vendor": "cursor", "auth": "login"},
                {"id": "key-fixture", "vendor": "cursor", "auth": "api-key", "key_file": str(key_file)},
            ]}))
        return subprocess.run(
            [sys.executable, str(REPO / "clients" / script), *args],
            env={**env, "MODELS": models, "MODELS_EXIT": str(models_exit), "KEY_MODELS": key_models},
            capture_output=True, text=True, check=False, timeout=20,
        )

    return run, tmp_path / "spawn"


@pytest.mark.parametrize("family", ["sonnet", "opus"])
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_route_cursor_claude_alias(cli, family: str, effort: str) -> None:
    run, _ = cli
    model = f"claude-{family}-5-5-{effort}"
    result = run("route", "resolve", f"{family}-latest-{effort}",
                 models=f"{model} - Standard\nclaude-{family}-6-{effort}-fast - Fast")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == model


def test_route_unknown_latest_alias(cli) -> None:
    run, _ = cli
    result = run("route", "resolve", "foo-latest")
    assert result.returncode == 3
    assert result.stderr.strip() == "route: unknown alias foo-latest"
    assert result.stdout == ""


@pytest.mark.parametrize("models,models_exit,allowed", [
    ("claude-opus-5-5-high - Opus\nclaude-opus-5-5-low - Opus Low", 0, False),
    ("claude-opus-5-5-medium - Opus", 0, True),
    ("", 0, True),
    ("claude-sonnet-5-5-high - Sonnet", 1, True),
])
def test_seat_cursor_preflight(cli, models: str, models_exit: int, allowed: bool) -> None:
    run, spawn = cli
    result = run("seat", "run", "--vendor", "cursor", "--model", "claude-opus-5-5-medium",
                 "--", "fixture prompt", models=models, models_exit=models_exit)
    assert spawn.exists() is allowed
    if allowed:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["result"] == "done"
    else:
        assert result.returncode != 0
        assert ("claude-opus-5-5-medium is not a Cursor model; closest: "
                "claude-opus-5-5-high, claude-opus-5-5-low") in result.stderr


def test_seat_superseded_warning_still_runs(cli) -> None:
    run, spawn = cli
    result = run("seat", "run", "--vendor", "cursor", "--model", "claude-opus-5-thinking-high",
                 "--", "fixture prompt", models=(
                     "claude-opus-5-thinking-high - Older\n"
                     "claude-opus-5-5-high - Current\nclaude-opus-6-high-fast - Fast"
                 ))
    assert result.returncode == 0, result.stderr
    assert spawn.exists()
    assert "superseded by claude-opus-5-5-high" in result.stderr


@pytest.mark.parametrize("pin", [None, "key-fixture", "fixture"])
def test_seat_cursor_model_list_uses_candidate_identity(cli, pin: str | None) -> None:
    run, spawn = cli
    args = ["run", "--vendor", "cursor", "--model", "claude-opus-5-5-high"]
    if pin:
        args += ["--account", pin]
    result = run("seat", *args, "--", "fixture prompt", api_key_account=True,
                 models="composer-2.5 - Composer", key_models="claude-opus-5-5-high - Opus")
    if pin == "fixture":
        assert result.returncode != 0
        assert not spawn.exists()
        assert "is not a Cursor model" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert spawn.exists()
        assert json.loads(result.stdout)["account"] == "key-fixture"


@pytest.mark.parametrize("models", ["", "composer-2.5 - Composer"])
def test_route_models_discovers_cursor_once_with_cold_cache(cli, models: str) -> None:
    run, spawn = cli
    result = run("route", "models", "--json", models=models)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["aliases"]
    assert spawn.with_suffix(".models").read_text().splitlines() == ["call"]


def test_route_resolve_skips_unknown_alias_when_later_name_resolves(cli) -> None:
    run, _ = cli
    result = run("route", "resolve", "foo-latest", "opus-latest-medium",
                 models="claude-opus-5-5-medium - Opus")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "claude-opus-5-5-medium"
    assert result.stderr == ""


def test_route_reports_unknown_aliases_only_after_all_names_fail(cli) -> None:
    run, _ = cli
    result = run("route", "resolve", "foo-latest", "bar-latest-high")
    assert result.returncode == 3
    assert result.stderr.strip() == "route: unknown alias foo-latest, bar-latest-high"
