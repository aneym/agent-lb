"""`of run` (Open Factory as a product, plan.md {#migrate}, Alex 2026-09-29 22:55 ET): one command picks a seat through
route, runs the brief on the right CLI, stands in on the next seat when a pool is out, and leaves a receipt."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.unit.test_route_ladder import reopen_cursor

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
        ROUTE_CURSOR_MODELS_CMD="printf 'grok-4.7-medium - Grok\\ncomposer-2.5 - Composer\\n'",
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
    # Grok, the implement head when its gate is open, is out of quota too; Composer answers.
    table = json.loads(TABLE.read_text())
    reopen_cursor(table)
    grok_open = tmp_path / "grok-open.json"
    grok_open.write_text(json.dumps(table), encoding="utf-8")
    env["ROUTE_TABLE"] = str(grok_open)
    seat = Path(env["OF_BIN_SEAT"])
    guard = "case \"$*\" in *grok-4.7-medium*) echo 'usage limit (429)' >&2; exit 1;; esac\n"
    _, logged, answer = seat.read_text().split("\n", 2)
    stub(seat, f"{logged}\n{guard}{answer}")
    done = of(env, "run", "implement", "--json", "--", "Rename helper x to y in a.py")
    assert done.returncode == 0, done.stderr
    receipt = json.loads(done.stdout)
    assert receipt["intended"] == {"seat": "cursor-seat", "model": "grok-4.7-medium"}
    assert receipt["ran"] == {"seat": "cursor-seat", "model": "composer-2.5"}
    assert receipt["standing_in"] is True
    assert [(a["seat"], a["outcome"]) for a in receipt["attempts"]] == [("cursor-seat", "limit"), ("cursor-seat", "ok")]
    assert all(set(attempt) == {"seat", "model", "pool", "maker", "outcome", "exit", "wall_s"}
               for attempt in receipt["attempts"])
    assert [(attempt["pool"], attempt["maker"]) for attempt in receipt["attempts"]] == [
        ("cursor", "xai"), ("cursor", "cursor")]
    assert "renamed" in Path(receipt["out"]).read_text()

    calls = rows(env, "CALLS")
    grok, cursor = calls[0], calls[1]
    assert grok["argv"][1:6] == ["run", "--vendor", "cursor", "--model", "grok-4.7-medium"]
    assert cursor["argv"][1:6] == ["run", "--vendor", "cursor", "--model", "composer-2.5"] and cursor["intent"] == "implement"

    ledger = rows(env, "ROUTE_LEDGER")
    decision = [r for r in ledger if r["event"] == "of_decision"]
    outcomes = [r for r in ledger if r["event"] == "of_outcome"]
    assert len(decision) == 1 and decision[0]["decision_id"] == receipt["decision_id"]
    assert (decision[0]["decider"], decision[0]["task_class"], decision[0]["seat"]) == ("ladder", "implement", "cursor-seat")
    assert [(o["decision_id"], o["attempt"], o["outcome"]) for o in outcomes] == [
        (receipt["decision_id"], 1, "limit"), (receipt["decision_id"], 2, "ok")]

    # --intended puts a job on the model it belongs on when the class menu has it: Composer through the seat CLI
    # (the interim mechanical ladder's Cursor rung; its Grok rung is grok-latest-low, not the medium alias).
    comp = json.loads(of(env, "run", "mechanical", "--intended", "composer-2.5", "--json", "--", "Rename y to z").stdout)
    assert comp["ran"] == {"seat": "cursor-seat", "model": "composer-2.5"} and comp["standing_in"] is False
    assert comp["attempts"][0]["maker"] == "cursor"
    seat = rows(env, "CALLS")[-1]["argv"]
    assert seat[1:6] == ["run", "--vendor", "cursor", "--model", "composer-2.5"] and "--mode" not in seat

    # An intended model the class menu lacks is recorded as intended, and route decides where the job runs.
    look = json.loads(of(env, "run", "explore", "--intended", "unavailable-model", "--json", "--", "Where is z?").stdout)
    assert look["intended"] == {"seat": None, "model": "unavailable-model"} and look["standing_in"] is True
    assert look["reason"] == "intended unavailable-model is not on the explore menu"


def test_of_installs_as_one_command(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    installed = subprocess.run([str(REPO / "clients" / "open-factory" / "install.sh"), "--prefix", str(prefix)],
                               capture_output=True, text=True, timeout=60, check=False)
    assert installed.returncode == 0, installed.stderr
    version = subprocess.run([str(prefix / "of"), "--version"], capture_output=True, text=True, timeout=60, check=False)
    assert version.stdout.startswith("open-factory 0.3")
    assert (REPO / "clients" / "open-factory" / "README.md").read_text().count("of run") >= 1


def test_exhausted_run_has_no_stand_in(tmp_path: Path) -> None:
    env = world(tmp_path)
    stub(Path(env["OF_BIN_CLAUDE_LB_LAUNCH"]), "echo 'usage limit'\nexit 1\n")
    stub(Path(env["OF_BIN_SEAT"]), "echo 'usage limit'\nexit 1\n")
    done = of(env, "run", "implement", "--json", "--", "Rename helper")
    receipt = json.loads(done.stdout)
    assert done.returncode == 2
    assert receipt["ran"] is None
    assert receipt["standing_in"] is False


def test_decision_pick_is_a_seat_id(tmp_path: Path) -> None:
    env = world(tmp_path)
    done = of(env, "run", "implement", "--json", "--", "Rename helper")
    assert done.returncode == 0, done.stderr
    decision = next(row for row in rows(env, "ROUTE_LEDGER") if row["event"] == "of_decision")
    assert decision["pick"] == json.loads(done.stdout)["intended"]["seat"]
    assert "." not in decision["pick"]


def test_intended_menu_seat_keeps_ladder_fallback(tmp_path: Path) -> None:
    env = world(tmp_path)
    first = {"seat": "gpt-implementer", "model": "gpt-6.1-sol", "pool": "openai-codex", "rung": "sol"}
    second = {"seat": "sonnet-implementer", "model": "claude-sonnet-5-5", "pool": "anthropic-general", "rung": "sonnet"}
    menu_seat = {key: value for key, value in first.items() if key != "rung"}
    menu = {"classes": {"implement": {"seats": [menu_seat]}}}
    env["OF_BIN_ROUTE"] = stub(tmp_path / "route", f'''case "$1" in
menu) echo '{json.dumps(menu)}' ;;
pick)
    case "$*" in
    *"--skip sol"*) echo '{json.dumps(second)}' ;;
    *) echo '{json.dumps(first)}' ;;
    esac ;;
esac
''')
    done = of(env, "run", "implement", "--intended", "gpt-6.1-sol", "--json", "--", "Rename helper")
    receipt = json.loads(done.stdout)
    assert done.returncode == 0, done.stderr
    assert receipt["ran"] == {"seat": "sonnet-implementer", "model": "claude-sonnet-5-5"}
    assert [attempt["outcome"] for attempt in receipt["attempts"]] == ["limit", "ok"]


def test_a_seat_capacity_wait_stands_in_and_verify_carries_the_author(tmp_path: Path) -> None:
    """A4d fix round: `seat run --class verify` needs the author vendor, and its exit 4 is a capacity wait."""
    env = world(tmp_path)
    cursor = {"seat": "cursor-seat", "model": "claude-sonnet-5-5-high", "pool": "cursor-other", "rung": "cursor"}
    sonnet = {"seat": "sonnet-verifier", "model": "claude-sonnet-5-5", "pool": "anthropic-general", "rung": "sonnet"}
    env["OF_BIN_ROUTE"] = stub(tmp_path / "route", f'''case "$*" in
    *"--skip cursor"*) echo '{json.dumps(sonnet)}' ;;
    *) echo '{json.dumps(cursor)}' ;;
esac
''')
    seat = Path(env["OF_BIN_SEAT"])
    _, logged, _ = seat.read_text().split("\n", 2)
    stub(seat, f"{logged}\necho '{{\"ok\": false, \"error\": \"capacity: all candidate capacity is reserved\"}}'\nexit 4\n")
    done = of(env, "run", "verify", "--author-vendor", "openai", "--json", "--", "Review the diff")
    receipt = json.loads(done.stdout)
    assert done.returncode == 0, done.stderr
    assert [(a["seat"], a["outcome"]) for a in receipt["attempts"]] == [("cursor-seat", "wait"), ("sonnet-verifier", "ok")]
    argv = next(call["argv"] for call in rows(env, "CALLS") if call["argv"][0].endswith("seat"))
    assert argv[argv.index("--author-vendor") + 1] == "openai"

    # With nothing to stand in on, a wait is the run's answer: exit 4, not a job failure.
    env["OF_BIN_ROUTE"] = stub(tmp_path / "route", f"echo '{json.dumps(cursor)}'\n")
    waited = of(env, "run", "verify", "--author-vendor", "openai", "--json", "--", "Review the diff")
    assert waited.returncode == 4 and json.loads(waited.stdout)["ran"] is None, waited.stdout
