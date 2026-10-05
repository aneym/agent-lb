"""Slow model discovery and unavailable workers must not stall the factory ladder."""
from __future__ import annotations

import json
import shlex
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.unit.test_open_factory_run import of, rows, stub, world
from tests.unit.test_route_ladder import CANONICAL_TABLE, pick, setup, table_copy


@pytest.mark.parametrize("vendor", ["cursor", "devin"])
def test_hung_model_list_skips_rung_and_caches_failure(tmp_path: Path, vendor: str) -> None:
    env = setup(tmp_path)
    calls = tmp_path / "model-calls"
    script = tmp_path / "hung.py"
    script.write_text(f"from pathlib import Path\nimport time\nPath({str(calls)!r}).write_text('called')\ntime.sleep(60)\n")
    env[f"ROUTE_{vendor.upper()}_MODELS_CMD"] = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    task = "implement" if vendor == "cursor" else "mechanical"
    args = ("--skip", "swe2-high") if vendor == "cursor" else ("--skip", "composer", "--skip", "grok-low")
    success = tmp_path / f"route-{vendor}-models.json"
    previous = json.dumps({"ts": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                           "models": ["grok-4.7-low" if vendor == "cursor" else "swe-2-medium"]})
    success.write_text(previous)
    started = time.monotonic()
    selected = pick(env, CANONICAL_TABLE, task, *args)
    assert time.monotonic() - started < 12
    assert selected["pool"] == "openai-codex"
    cache = tmp_path / f"route-{vendor}-models.failed.json"
    assert json.loads(cache.read_text())["ts"]
    assert success.read_text() == previous
    cache.write_text(json.dumps({"ts": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}))
    calls.unlink()
    started = time.monotonic()
    assert pick(env, CANONICAL_TABLE, task, *args)["pool"] == "openai-codex"
    assert time.monotonic() - started < 2
    assert not calls.exists()
    # A negative result expires after five minutes, not after the success cache's day.
    cached = json.loads(cache.read_text())
    cached["ts"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    cache.write_text(json.dumps(cached))
    script.write_text(f"from pathlib import Path\nPath({str(calls)!r}).write_text('called')\n")
    pick(env, CANONICAL_TABLE, task, *args)
    assert calls.exists()


@pytest.mark.parametrize("code,message", [
    (2, '{"vendor":"cursor","attempts":[],"ok":false,"error":"no account ready"}'),
    (1, "out of credits"),
    (1, "resource_exhausted"),
    (1, "limit reached"),
])
@pytest.mark.parametrize("pool", ["cursor-models", "devin"])
def test_seat_pool_miss_runs_next_rung(tmp_path: Path, code: int, message: str, pool: str) -> None:
    env = world(tmp_path)
    first = {"seat": "cursor-seat" if pool.startswith("cursor") else "devin-seat",
             "model": "grok-4.7-low" if pool.startswith("cursor") else "swe-2-high",
             "pool": pool, "rung": "worker"}
    second = {"seat": "sonnet-implementer", "model": "claude-sonnet-5-5",
              "pool": "anthropic-general", "rung": "sonnet"}
    env["OF_BIN_ROUTE"] = stub(tmp_path / "route", f'''case "$*" in
*"--skip worker"*) echo '{json.dumps(second)}' ;;
*) echo '{json.dumps(first)}' ;;
esac
''')
    stream = "" if code == 2 else " >&2"
    stub(Path(env["OF_BIN_SEAT"]), f"echo '{message}'{stream}\nexit {code}\n")
    done = of(env, "run", "implement", "--json", "--", "Rename helper")
    assert done.returncode == 0, done.stderr
    receipt = json.loads(done.stdout)
    assert receipt["ran"] == {"seat": second["seat"], "model": second["model"]}
    assert [attempt["outcome"] for attempt in receipt["attempts"]] == ["limit", "ok"]


@pytest.mark.parametrize("vendor", ["cursor", "devin"])
def test_read_only_rung_reaches_seat_adapter(tmp_path: Path, vendor: str) -> None:
    env = world(tmp_path)
    routing = tmp_path / "routing"
    routing.mkdir()
    env.update(setup(routing))

    def readonly_worker(table):
        rung = next(row for row in table["ladders"]["interim"]["implement"]
                    if row["id"] == ("grok-medium" if vendor == "cursor" else "swe2-high"))
        rung["read_only"] = True
        rung.pop("gate", None)
        table["ladders"]["interim"]["implement"] = [rung]

    table = table_copy(tmp_path, "readonly", readonly_worker)
    env["ROUTE_TABLE"] = str(table)
    selected = pick(env, table, "implement")
    assert selected["read_only"] is True
    done = of(env, "run", "implement", "--json", "--", "Inspect helper")
    assert done.returncode == 0, done.stderr
    argv = rows(env, "CALLS")[-1]["argv"]
    assert argv[argv.index("--vendor") + 1] == vendor
    assert argv[argv.index("--mode") + 1] == "ask"


def test_seat_argparse_exit_stays_a_hard_failure(tmp_path: Path) -> None:
    env = world(tmp_path)
    first = {"seat": "cursor-seat", "model": "grok-4.7-low", "pool": "cursor-models", "rung": "worker"}
    env["OF_BIN_ROUTE"] = stub(tmp_path / "route", f"echo '{json.dumps(first)}'\n")
    stub(Path(env["OF_BIN_SEAT"]), "echo 'usage: seat run; error: unrecognized arguments: --bad' >&2\nexit 2\n")
    done = of(env, "run", "implement", "--json", "--", "Rename helper")
    assert done.returncode == 1
    receipt = json.loads(done.stdout)
    assert receipt["ran"] is None
    assert [attempt["outcome"] for attempt in receipt["attempts"]] == ["fail"]


def test_both_hung_model_lists_leave_pick_within_factory_timeout(tmp_path: Path) -> None:
    env = setup(tmp_path)
    script = tmp_path / "hung.py"
    script.write_text("import time\ntime.sleep(60)\n")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    env.update(ROUTE_CURSOR_MODELS_CMD=command, ROUTE_DEVIN_MODELS_CMD=command)
    started = time.monotonic()
    selected = pick(env, CANONICAL_TABLE, "implement")
    assert time.monotonic() - started < 25
    assert selected["pool"] == "openai-codex"
    assert all((tmp_path / f"route-{vendor}-models.failed.json").exists() for vendor in ("cursor", "devin"))


def test_non_utf8_stderr_yields_receipt_and_failure_reason(tmp_path: Path) -> None:
    env = world(tmp_path)
    first = {"seat": "cursor-seat", "model": "grok-4.7-low", "pool": "cursor-models", "rung": "worker"}
    env["OF_BIN_ROUTE"] = stub(tmp_path / "route", f"echo '{json.dumps(first)}'\n")
    stub(Path(env["OF_BIN_SEAT"]), "echo stdout\nprintf '\\377' >&2\nexit 1\n")
    done = of(env, "run", "implement", "--json", "--", "Rename helper")
    assert done.returncode == 1, done.stderr
    receipt = json.loads(done.stdout)
    assert receipt["attempts"][0]["outcome"] == "fail"
    assert receipt["reason"].endswith("\ufffd")
    assert Path(receipt["out"]).with_suffix(".stderr").read_bytes() == b"\xff"
    assert Path(receipt["out"]).read_text() == "stdout\n"
