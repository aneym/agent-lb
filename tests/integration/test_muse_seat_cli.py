
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from app.modules.pools.cli_seats import cli_seat_pools, read_seat_accounts

REPO = Path(__file__).resolve().parents[2]


def test_muse_exec_receipt_pool_limit_and_version_pin(tmp_path: Path) -> None:
    vendor = tmp_path / "muse"
    vendor.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
    print('Muse Code 1.4.7 (1.4.7-R6101.1)' if not Path('wrong-version').exists() else 'Muse Code 1.4.6')
    raise SystemExit(0)
a = sys.argv[1:]
assert a[0] == 'exec'
assert '--json' in a
assert a[a.index('--effort') + 1] == 'medium'
assert 'META_API_KEY' not in os.environ
assert '--trust-workspace' in a
assert a[a.index('--contributor-mode') + 1] == 'off'
assert a[a.index('--approval-mode') + 1] == 'never'
assert a[a.index('--model') + 1] == 'muse-wonder-1.0'
assert '--disable-sandbox' not in a and '--yolo' not in a
assert Path(a[a.index('--prompt-file') + 1]).read_text().startswith('Create result.txt')
if Path('limited').exists():
    print('usage limit reached; try again in 2 hours', file=sys.stderr)
    raise SystemExit(1)
Path('result.txt').write_text('Muse seat check\\n')
print(json.dumps({'id':'event-1', 'stream':{'kind':'session','id':'00000000-0000-4000-8000-000000000001'}, 'payload_type':'run.terminal.completed', 'payload':{'terminal':'completed','text':'Created result.txt'}}))
''')
    vendor.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SEAT_", "ROUTE_", "META_"))}
    env.update(SEAT_HOME=str(tmp_path / "seats"), ROUTE_BIN=str(REPO / "clients/route"),
               ROUTE_TABLE=str(REPO / "config/coding-agents/routing-table.json"),
               ROUTE_LEDGER=str(tmp_path / "dispatch.jsonl"), SEAT_MUSE_BIN=str(vendor),
               META_API_KEY="synthetic-test-key")

    def seat(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-c", "import runpy,sys; sys.platform='darwin'; "
                               "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')",
                               str(REPO / "clients/seat"), *args], env=env,
                              cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)

    if sys.platform != "darwin":
        refused = subprocess.run([sys.executable, str(REPO / "clients/seat"), "add", "muse-box",
                                  "--vendor", "muse"], env=env, cwd=tmp_path, capture_output=True, text=True)
        assert refused.returncode != 0 and "Studio-local" in refused.stderr
    added = seat("add", "muse-test", "--vendor", "muse")
    assert added.returncode == 0, added.stderr
    assert "auth ok" in added.stdout
    probe = seat("probe", "--vendor", "muse", "--json")
    assert probe.returncode == 0
    assert json.loads(probe.stdout)["muse-test"]["identity"]["auth_source"] == "existing-login-unverified"
    args = ("run", "--vendor", "muse", "--account", "muse-test", "--model", "muse-latest",
            "--cwd", str(tmp_path), "--", "Create result.txt")
    completed = seat(*args)
    assert completed.returncode == 0, completed.stderr
    outcome = json.loads(completed.stdout)
    UUID(outcome["vendor_session_id"])
    assert outcome["result"] == "Created result.txt"
    assert outcome["model"] == "muse-wonder-1.0"
    assert outcome["tokens_in"] is None and outcome["tokens_out"] is None
    assert (tmp_path / "result.txt").read_text() == "Muse seat check\n"
    rows = [json.loads(line) for line in (tmp_path / "dispatch.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["dispatch", "closeout"]
    assert rows[1]["usage_source"] == "unavailable"
    pool = cli_seat_pools(read_seat_accounts(tmp_path / "seats/state.json"))[0]
    assert pool.id == "muse" and pool.observed_runs == 1
    assert pool.observed_tokens is None and pool.headroom_percent is None
    state = json.loads((tmp_path / "seats/state.json").read_text())
    assert state["accounts"]["muse-test"]["runs"][0]["tokens_in"] is None
    fixtures = tmp_path / "http-fixtures"
    fixtures.mkdir()
    (fixtures / "api_pools.json").write_text(json.dumps({"pools": [pool.model_dump(mode="json")]}))
    pool_env = dict(env, ROUTE_FIXTURE_DIR=str(fixtures), ROUTE_POOL_HISTORY=str(tmp_path / "pool-history"))
    pools = subprocess.run([sys.executable, str(REPO / "clients/route"), "pools", "--json"], env=pool_env,
                           capture_output=True, text=True, timeout=30, check=False)
    assert pools.returncode == 0, pools.stderr
    assert "muse" in pools.stdout and "cli_seat" in pools.stdout
    (tmp_path / "limited").touch()
    limited = seat(*args)
    assert limited.returncode == 2, limited.stderr
    pool = cli_seat_pools(read_seat_accounts(tmp_path / "seats/state.json"))[0]
    assert pool.status == "exhausted" and pool.reset_at is not None
    assert pool.observed_runs == 2
    (tmp_path / "wrong-version").touch()
    wrong = seat(*args)
    assert wrong.returncode != 0 and "requires pinned" in wrong.stderr
    assert len((tmp_path / "dispatch.jsonl").read_text().splitlines()) == 4
