"""`of run` (Open Factory as a product, plan.md {#migrate}, Alex 2026-09-29 22:55 ET): one command picks a seat through
route, runs the brief on the right CLI, stands in on the next seat when a pool is out, and leaves a receipt."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OF = REPO / "clients" / "open-factory" / "bin" / "open-factory"
TABLE = REPO / "config" / "coding-agents" / "routing-table.json"


def stub(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def world(tmp_path: Path) -> dict[str, str]:
    fixtures, home, bins = tmp_path / "fixtures", tmp_path / "home", tmp_path / "bin"
    for directory in (fixtures, home / ".claude", bins):
        directory.mkdir(parents=True)
    (fixtures / "api_models.json").write_text(json.dumps({"models": [
        {"id": "gpt-6.1-sol"}, {"id": "claude-sonnet-5-5"}, {"id": "claude-opus-5-5"}]}))
    (fixtures / "api_pools.json").write_text(json.dumps({"pools": [
        {"id": "openai-codex", "status": "ok", "accounts": 6, "eligibleAccounts": 5, "headroomPercent": 70.0},
        {"id": "anthropic-general", "status": "ok", "accounts": 8, "eligibleAccounts": 4, "headroomPercent": 70.0,
         "weeklyPacePercent": 5.0},
        {"id": "cursor", "status": "ok", "accounts": 1, "eligibleAccounts": 1}]}))
    calls = tmp_path / "calls.jsonl"
    # Each stub logs its argv and the intent tag, then answers like the real CLI would.
    log = f'printf \'%s\\n\' "$(python3 -c \'import json,os,sys; print(json.dumps({{"argv": sys.argv[1:], "intent": os.environ.get("AGENT_LB_INTENT")}}))\' "$0" "$@")" >> {calls}\n'
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "AGENT_LB_", "CLAUDE", "OF_"))}
    env.update(
        HOME=str(home), PATH=f"{bins}:/usr/bin:/bin", AGENT_LB_URL="http://127.0.0.1:1",
        ROUTE_TABLE=str(TABLE), ROUTE_FIXTURE_DIR=str(fixtures), ROUTE_MODELS_CACHE=str(tmp_path / "models.json"),
        ROUTE_LEDGER=str(tmp_path / "dispatch.jsonl"),
        ROUTE_CURSOR_MODELS_CMD="printf 'grok-4.7-medium-fast - Grok\\ncomposer-2.5 - Composer\\n'",
        # Codex is out of quota; Claude and Cursor answer.
        OF_BIN_CODEX=stub(bins / "codex", log + "echo 'ERROR: You have hit your usage limit (429)' >&2\nexit 1\n"),
        OF_BIN_CLAUDE_LB_LAUNCH=stub(bins / "claude-lb-launch", log + "echo '{\"result\": \"renamed\"}'\n"),
        OF_BIN_SEAT=stub(bins / "seat", log + "echo '{\"ok\": true, \"result\": \"renamed\"}'\n"),
        CALLS=str(calls),
    )
    return env


def of(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(OF), *args], capture_output=True, text=True, timeout=60, env=env,
                          cwd=env["HOME"], check=False)


def rows(env: dict[str, str], name: str) -> list[dict]:
    path = Path(env[name])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_of_run_stands_in_when_codex_is_out_and_leaves_a_receipt(tmp_path: Path) -> None:
    env = world(tmp_path)

    done = of(env, "run", "implement", "--json", "--", "Rename helper x to y in a.py")
    assert done.returncode == 0, done.stderr
    receipt = json.loads(done.stdout)
    assert receipt["intended"] == {"seat": "gpt-implementer", "model": "gpt-6.1-sol"}
    assert receipt["ran"] == {"seat": "sonnet-implementer", "model": "claude-sonnet-5-5"}
    assert receipt["standing_in"] is True
    assert [(a["seat"], a["outcome"]) for a in receipt["attempts"]] == [("gpt-implementer", "limit"), ("sonnet-implementer", "ok")]
    assert "renamed" in Path(receipt["out"]).read_text()

    calls = rows(env, "CALLS")
    codex, claude = calls[0], calls[1]
    assert codex["argv"][1:4] == ["exec", "-m", "gpt-6.1-sol"] and codex["argv"][-1] == "Rename helper x to y in a.py"
    assert claude["argv"][1:4] == ["-p", "--model", "claude-sonnet-5-5"] and claude["intent"] == "implement"

    ledger = rows(env, "ROUTE_LEDGER")
    decision = [r for r in ledger if r["event"] == "of_decision"]
    outcomes = [r for r in ledger if r["event"] == "of_outcome"]
    assert len(decision) == 1 and decision[0]["decision_id"] == receipt["decision_id"]
    assert (decision[0]["decider"], decision[0]["task_class"], decision[0]["seat"]) == ("route", "implement", "gpt-implementer")
    assert [(o["decision_id"], o["attempt"], o["outcome"]) for o in outcomes] == [
        (receipt["decision_id"], 1, "limit"), (receipt["decision_id"], 2, "ok")]

    # --intended puts a job on the model it belongs on when the class menu has it: Grok through the seat CLI.
    grok = json.loads(of(env, "run", "mechanical", "--intended", "grok-latest", "--json", "--", "Rename y to z").stdout)
    assert grok["ran"] == {"seat": "cursor-seat", "model": "grok-4.7-medium-fast"} and grok["standing_in"] is False
    seat = rows(env, "CALLS")[-1]["argv"]
    assert seat[1:6] == ["run", "--vendor", "cursor", "--model", "grok-4.7-medium-fast"] and "--mode" not in seat

    # An intended model the class menu lacks is recorded as intended, and route decides where the job runs.
    look = json.loads(of(env, "run", "explore", "--intended", "composer-2.5", "--json", "--", "Where is z?").stdout)
    assert look["intended"] == {"seat": None, "model": "composer-2.5"} and look["standing_in"] is True
    assert look["reason"] == "intended composer-2.5 is not on the explore menu"


def test_of_installs_as_one_command(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    installed = subprocess.run([str(REPO / "clients" / "open-factory" / "install.sh"), "--prefix", str(prefix)],
                               capture_output=True, text=True, timeout=60, check=False)
    assert installed.returncode == 0, installed.stderr
    version = subprocess.run([str(prefix / "of"), "--version"], capture_output=True, text=True, timeout=60, check=False)
    assert version.stdout.startswith("open-factory 0.3")
    assert (REPO / "clients" / "open-factory" / "README.md").read_text().count("of run") >= 1
