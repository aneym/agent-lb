"""Exercise lease bundles and releases through the real seat CLI and vendor stand-ins."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.pools.cli_seats import cli_seat_pools, read_seat_accounts

SEAT = Path(__file__).resolve().parents[2] / "clients" / "seat"
CURSOR = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
key = os.environ.get('CURSOR_API_KEY', '')
def listed(name):
    path = Path(os.environ[name])
    return path.exists() and key in path.read_text().splitlines()
if sys.argv[1:] == ['models']:
    if listed('FAKE_REVOKED_KEYS'):
        print('Error: invalid api key ' + key, file=sys.stderr)
        raise SystemExit(1)
    if listed('FAKE_PROBE_LIMITED_KEYS'):
        print('Error: 429 too many requests ' + key, file=sys.stderr)
        raise SystemExit(1)
    print('Available models')
    raise SystemExit(0)
if os.environ.get('FAKE_GATE'):
    Path(os.environ['FAKE_STARTED']).touch()
    for _ in range(300):
        if Path(os.environ['FAKE_GATE']).exists():
            break
        time.sleep(.1)
    else:
        raise SystemExit(1)
if listed('FAKE_LIMITED_KEYS'):
    print("Error: You've hit your usage limit. Try again in 2 hours.", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps({'type': 'result', 'is_error': False, 'result': 'done',
                  'session_id': 'chat-fake',
                  'usage': {'inputTokens': 120, 'outputTokens': 7, 'cacheReadTokens': 0}}))
"""
DEVIN = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
if sys.argv[1:] == ['auth', 'status']:
    if Path(os.environ['FAKE_DEVIN_REVOKED']).exists():
        print('Not logged in ' + (Path(os.environ['XDG_DATA_HOME']) / 'devin/credentials.toml').read_text())
        raise SystemExit(1)
    print('Logged in')
"""
ROUTE = "#!/bin/sh\necho grok-9-medium-fast\n"


@pytest.fixture
def home(tmp_path: Path) -> tuple[dict[str, str], Path, str, str]:
    return home_values(tmp_path)


def home_values(tmp_path: Path) -> tuple[dict[str, str], Path, str, str]:
    cursor_key = "SENTINEL-" + uuid.uuid4().hex
    devin_key = "SENTINEL-" + uuid.uuid4().hex
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SEAT_", "ROUTE_", "CURSOR_", "XDG_"))}
    env.update(
        HOME=str(tmp_path),
        SEAT_HOME=str(tmp_path / "seats"),
        ROUTE_LEDGER=str(tmp_path / "ledger.jsonl"),
        ROUTE_BIN=str(script(tmp_path / "route", ROUTE)),
        SEAT_CURSOR_BIN=str(script(tmp_path / "cursor-agent", CURSOR)),
        SEAT_DEVIN_BIN=str(script(tmp_path / "devin", DEVIN)),
        FAKE_LIMITED_KEYS=str(tmp_path / "limited"),
        FAKE_REVOKED_KEYS=str(tmp_path / "revoked"),
        FAKE_PROBE_LIMITED_KEYS=str(tmp_path / "probe-limited"),
        FAKE_DEVIN_REVOKED=str(tmp_path / "devin-revoked"),
    )
    return env, tmp_path, cursor_key, devin_key


def script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def seat(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SEAT), *args], env=env, capture_output=True, text=True, timeout=60)


def add_cursor(env: dict[str, str], root: Path, name: str, key: str) -> None:
    path = root / f"{name}.key"
    path.write_text(key + "\n")
    path.chmod(0o600)
    result = seat(env, "add", name, "--vendor", "cursor", "--api-key-file", str(path))
    assert result.returncode == 0, result.stderr


def add_devin(env: dict[str, str], root: Path, key: str) -> Path:
    data = root / "devin-data"
    (data / "devin" / "cli").mkdir(parents=True)
    credential = data / "devin" / "credentials.toml"
    credential.write_text(key)
    (data / "devin" / "sessions.db").write_bytes(b"session")
    (data / "devin" / "cli" / "trusted_workspaces.json").write_text('{"trusted_paths": ["/unrelated"]}')
    result = seat(env, "add", "devin-a", "--vendor", "devin", "--data-dir", str(data))
    assert result.returncode == 0, result.stderr
    return credential


def lease(env: dict[str, str], root: Path, vendor: str = "cursor", *extra: str) -> dict:
    args = ["lease", "--vendor", vendor, "--for", "unit-1", "--ttl", "3600", "--out", str(root / uuid.uuid4().hex)]
    if vendor == "devin":
        args += ["--trust", "/w/one"]
    result = seat(env, *args, *extra, "--json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def run(env: dict[str, str], root: Path, *extra: str) -> dict:
    result = seat(env, "run", "--vendor", "cursor", "--model", "grok-latest", "--cwd", str(root), *extra, "--", "task")
    assert result.returncode in (0, 2), (result.stdout, result.stderr)
    return json.loads(result.stdout)


def records(root: Path) -> dict:
    return json.loads((root / "seats" / "state.json").read_text())


def ledger(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "ledger.jsonl").read_text().splitlines()]


def test_pick_and_spread(home: tuple) -> None:
    env, root, key, _ = home
    for name, value in (("acct-a", key), ("acct-b", "key-b"), ("acct-c", "key-c")):
        add_cursor(env, root, name, value)
    (root / "limited").write_text(key)
    first = run(env, root)
    assert [a["account"] for a in first["attempts"]] == ["acct-a", "acct-b"]
    copy = root / "copy"
    shutil.copytree(root / "seats", copy)
    copied = dict(env, SEAT_HOME=str(copy), ROUTE_LEDGER=str(root / "copy-ledger"))
    assert run(copied, root)["account"] == "acct-c"
    assert lease(env, root)["account"] == "acct-c"
    # A lease names no model, and acct-a's limit cooled only the Grok pool
    # (cursor-pool-truth), so acct-a, used before acct-b, is next.
    assert lease(env, root)["account"] == "acct-a"
    assert lease(env, root)["account"] == "acct-b"


def test_concurrent_leases_spread(home: tuple) -> None:
    env, root, _, _ = home
    add_cursor(env, root, "acct-b", "key-b")
    add_cursor(env, root, "acct-c", "key-c")
    jobs = [
        subprocess.Popen(
            [
                sys.executable,
                str(SEAT),
                "lease",
                "--vendor",
                "cursor",
                "--for",
                f"unit-{i}",
                "--ttl",
                "3600",
                "--out",
                str(root / f"bundle-{i}"),
                "--json",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(2)
    ]
    results = [job.communicate(timeout=60) for job in jobs]
    assert all(job.returncode == 0 for job in jobs), results
    leases = [json.loads(output) for output, _ in results]
    assert {item["account"] for item in leases} == {"acct-b", "acct-c"}
    rows = {row["id"]: row for row in json.loads(seat(env, "accounts", "--json").stdout)["accounts"]}
    for item in leases:
        assert rows[item["account"]]["leases"] == {"active": 1, "next_expiry": item["expires_at"]}


def test_cursor_bundle_and_modes(home: tuple) -> None:
    env, root, key, _ = home
    add_cursor(env, root, "acct-a", key)
    out = root / "bundle"
    out.mkdir(mode=0o755)
    result = seat(env, "lease", "--vendor", "cursor", "--for", "unit-1", "--ttl", "60", "--out", str(out), "--json")
    assert result.returncode == 0, result.stderr
    meta = json.loads(result.stdout)
    assert set(meta) == {"lease", "vendor", "account", "expires_at"} and meta["account"] == "acct-a"
    assert json.loads((out / "lease.json").read_text()) == meta
    assert (out / "env").read_text() == f"CURSOR_API_KEY={shlex.quote(key)}\nAGENT_CLI_CREDENTIAL_STORE=memory\n"
    assert list((out / "data").iterdir()) == []
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in (out, out / "data"))
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (out / "env", out / "lease.json"))
    assert key not in result.stdout + result.stderr + (out / "lease.json").read_text() + json.dumps(records(root))


def test_duplicate_prompt_add_preserves_key(home: tuple) -> None:
    env, root, key, _ = home
    first = subprocess.run(
        [sys.executable, str(SEAT), "add", "acct-a", "--vendor", "cursor", "--api-key-prompt"],
        input=key + "\n",
        env=env,
        capture_output=True,
        text=True,
        start_new_session=True,
        timeout=60,
    )
    assert first.returncode == 0, first.stderr
    stored = root / "seats" / "keys" / "acct-a.key"
    original = stored.read_bytes()
    attempted = subprocess.run(
        [sys.executable, str(SEAT), "add", "acct-a", "--vendor", "cursor", "--api-key-prompt"],
        input="OVERWRITE-invented\n",
        env=env,
        capture_output=True,
        text=True,
        start_new_session=True,
        timeout=60,
    )
    assert attempted.returncode == 1 and "already registered" in attempted.stderr
    assert stored.read_bytes() == original


def test_devin_bundle(home: tuple) -> None:
    env, root, _, key = home
    credential = add_devin(env, root, key)
    out = root / "bundle"
    result = seat(
        env,
        "lease",
        "--vendor",
        "devin",
        "--for",
        "unit-1",
        "--ttl",
        "60",
        "--out",
        str(out),
        "--trust",
        "/w/one",
        "--trust",
        "/w/two",
        "--trust",
        "/w/one",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    files = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert files == {"env", "lease.json", "data/devin/credentials.toml", "data/devin/cli/trusted_workspaces.json"}
    assert (out / "env").read_bytes() == b""
    assert (out / "data/devin/credentials.toml").read_bytes() == credential.read_bytes()
    assert json.loads((out / "data/devin/cli/trusted_workspaces.json").read_text()) == {
        "trusted_paths": ["/w/one", "/w/two"]
    }
    assert all(
        p.stat().st_mode & 0o777 == 0o700 for p in (out, out / "data", out / "data/devin", out / "data/devin/cli")
    )
    assert all((out / p).stat().st_mode & 0o777 == 0o600 for p in files)
    assert (
        seat(
            env, "lease", "--vendor", "devin", "--for", "unit-2", "--ttl", "1", "--out", str(root / "no-trust")
        ).returncode
        == 2
    )


def test_login_accounts_never_leased(home: tuple) -> None:
    env, root, _, _ = home
    result = seat(env, "add", "acct-login", "--vendor", "cursor")
    assert "registered acct-login" in result.stdout
    out = root / "bundle"
    args = ("lease", "--vendor", "cursor", "--for", "unit-1", "--ttl", "60", "--out", str(out), "--json")
    failed = seat(env, *args)
    assert failed.returncode == 2 and json.loads(failed.stdout)["error"] == "no_account"
    assert json.loads(failed.stdout)["skipped"] == [{"account": "acct-login", "reason": "login_not_leasable"}]
    assert "acct-login" in failed.stderr and not out.exists()
    pinned = seat(env, *args, "--account", "acct-login")
    assert pinned.returncode == 2 and json.loads(pinned.stdout)["error"] == "not_leasable"


def test_expired_lease_stops_counting(home: tuple) -> None:
    env, root, _, _ = home
    add_cursor(env, root, "acct-b", "key-b")
    add_cursor(env, root, "acct-c", "key-c")
    assert lease(env, root, "cursor", "--ttl", "1")["account"] == "acct-b"
    assert lease(env, root)["account"] == "acct-c"
    time.sleep(2)
    rows = {row["id"]: row for row in json.loads(seat(env, "accounts", "--json").stdout)["accounts"]}
    assert rows["acct-b"]["leases"]["active"] == 0
    assert lease(env, root)["account"] == "acct-b"


@pytest.mark.parametrize("variant", ["limit", "auth_rejected", "auth_ok", "auth_limited", "success"])
def test_release_outcomes_as_the_lb_sees_them(tmp_path: Path, variant: str) -> None:
    env, root, key, _ = home_values(tmp_path)
    add_cursor(env, root, "acct-a", key)
    meta = lease(env, root)
    before = records(root)["accounts"]["acct-a"]
    log = root / "seat.log"
    log.write_text(
        "transcript\nError: "
        + (
            "You've hit your usage limit. Try again in 2 hours. "
            if variant in ("limit", "success")
            else "invalid api key "
        )
        + key
    )
    if variant == "auth_rejected":
        (root / "revoked").write_text(key)
    if variant == "auth_limited":
        (root / "probe-limited").write_text(key)
    flags = ("--outcome", "ok") if variant == "success" else ()
    result = seat(env, "release", meta["lease"], "--log", str(log), *flags, "--json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    answer = json.loads(result.stdout)
    record = records(root)["accounts"]["acct-a"]
    pool = cli_seat_pools(read_seat_accounts(root / "seats" / "state.json"))[0]
    assert record["runs"][-1]["run_id"] == meta["lease"]
    assert [row["event"] for row in ledger(root)] == ["lease", "release"]
    assert {row["session_id"] for row in ledger(root)} == {meta["lease"]}
    if variant == "limit":
        # A Cursor limit cools one pool, not the account; a release without --model
        # names no model, so the limit lands on the catch-all cursor-other pool.
        assert not record.get("cooldown_until")
        until = datetime.fromisoformat(record["cooldowns"]["cursor-other"].replace("Z", "+00:00"))
        assert abs((until - datetime.now(timezone.utc) - timedelta(hours=2)).total_seconds()) < 60
        assert record["last_error"]["kind"] == "limit"
        assert record["last_error"]["text"] == "limit in seat log: usage limit; Try again in 2 hour"
        assert (pool.status, pool.eligible_accounts) == ("ok", 1)
    elif variant == "auth_rejected":
        assert (answer["auth_probe"], answer["auth_confirmed"], record["auth_ok"]) == ("rejected", True, False)
        assert pool.status == "exhausted"
    elif variant == "auth_ok":
        assert (answer["auth_probe"], answer["auth_confirmed"], record["auth_ok"]) == ("ok", False, True)
        assert record["last_error"]["kind"] == "auth_unconfirmed" and pool.status == "ok"
    elif variant == "auth_limited":
        assert (answer["auth_probe"], answer["auth_confirmed"]) == ("inconclusive", False)
        assert all(record.get(field) == before.get(field) for field in ("auth_ok", "auth_checked_at", "auth_error"))
        assert record["last_error"]["kind"] == "auth_unconfirmed" and pool.status == "ok"
    else:
        assert answer["outcome"] == "ok" and not answer["cooldown_until"]
        assert record["last_error"] is None and record["auth_ok"] and pool.status == "ok"


def test_release_is_idempotent_and_strict(home: tuple) -> None:
    env, root, key, _ = home
    add_cursor(env, root, "acct-a", key)
    meta = lease(env, root)
    assert seat(env, "release", meta["lease"], "--outcome", "ok", "--json").returncode == 0
    before = (root / "seats/state.json").read_bytes()
    lines = len(ledger(root))
    again = seat(env, "release", meta["lease"], "--json")
    assert again.returncode == 0 and json.loads(again.stdout)["reason"] == "already_released"
    assert json.loads(again.stdout)["released"] is False
    assert (root / "seats/state.json").read_bytes() == before and len(ledger(root)) == lines
    missing = seat(env, "release", "lease-0000", "--json")
    assert missing.returncode == 2 and json.loads(missing.stdout)["error"] == "unknown_lease"


def test_writers_keep_each_others_changes(home: tuple) -> None:
    env, root, key, _ = home
    add_cursor(env, root, "acct-a", key)
    add_cursor(env, root, "acct-b", "key-b")
    gated = dict(env, FAKE_GATE=str(root / "gate"), FAKE_STARTED=str(root / "started"))
    job = subprocess.Popen(
        [
            sys.executable,
            str(SEAT),
            "run",
            "--vendor",
            "cursor",
            "--model",
            "grok-latest",
            "--account",
            "acct-a",
            "--cwd",
            str(root),
            "--",
            "task",
        ],
        env=gated,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(300):
            if (root / "started").exists():
                break
            time.sleep(0.1)
        assert (root / "started").exists()
        meta = lease(env, root, "cursor", "--account", "acct-b")
        add_cursor(env, root, "acct-new", "key-new")
    finally:
        (root / "gate").touch()
        output, error = job.communicate(timeout=60)
    assert job.returncode == 0, (output, error)
    state = records(root)
    assert state["leases"][meta["lease"]]["account"] == "acct-b"
    assert state["accounts"]["acct-new"]["auth_ok"] is True
    assert state["accounts"]["acct-new"]["auth_checked_at"]
    assert state["accounts"]["acct-a"]["runs"][-1]["run_id"]
    assert seat(env, "release", meta["lease"], "--outcome", "ok", "--json").returncode == 0


def test_box_run_uses_shipped_lease(home: tuple) -> None:
    env, root, key, _ = home
    add_cursor(env, root, "acct-a", key)
    out = root / "bundle"
    issued = seat(
        env, "lease", "--vendor", "cursor", "--for", "unit-1", "--ttl", "3600", "--out", str(out), "--json"
    )
    assert issued.returncode == 0, issued.stderr
    meta = json.loads(issued.stdout)
    box = root / "box"
    dest = box / "leases" / meta["lease"]
    dest.parent.mkdir(parents=True)
    shutil.move(out, dest)
    saw = root / "saw-key"
    cursor = script(
        root / "box-cursor",
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ["SAW_KEY"]).write_text(os.environ.get("CURSOR_API_KEY", ""))
if sys.argv[1:] == ["models"]:
    print("Available models")
    raise SystemExit(0)
print(json.dumps({"type": "result", "is_error": False, "result": "done",
                  "usage": {"inputTokens": 120, "outputTokens": 7, "cacheReadTokens": 4}}))
""",
    )
    box_env = dict(env, SEAT_HOME=str(box), SEAT_CURSOR_BIN=str(cursor), SAW_KEY=str(saw))
    result = seat(box_env, "run", "--vendor", "cursor", "--model", "grok-latest", "--cwd", str(root), "--", "task")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert saw.read_text() == key
    lines = (dest / "usage.jsonl").read_text().splitlines()
    assert len(lines) == 1
    recorded = json.loads(lines[0])
    assert set(recorded) == {"ts", "model", "ok", "wall_s", "tokens_in", "tokens_out", "cache_read_tokens"}
    assert recorded["ok"] is True and recorded["tokens_in"] == 120
    usage = json.loads((dest / "usage.json").read_text())
    assert usage["tokens_in"] == 120 and usage["tokens_out"] == 7 and usage["cache_read_tokens"] == 4
    assert usage["model"] == recorded["model"]
    listed = seat(box_env, "accounts", "--json")
    assert listed.returncode == 0, listed.stderr
    assert key not in listed.stdout
    rows = json.loads(listed.stdout)["accounts"]
    assert len(rows) == 1 and rows[0]["auth"] == "lease" and rows[0]["expires_at"] == meta["expires_at"]
    assert rows[0]["id"] == "acct-a"

    stale = json.loads((dest / "lease.json").read_text())
    stale["expires_at"] = "2000-01-01T00:00:00Z"
    (dest / "lease.json").write_text(json.dumps(stale))
    expired = seat(box_env, "run", "--vendor", "cursor", "--model", "grok-latest", "--cwd", str(root), "--", "task")
    assert expired.returncode == 2 and "no cursor account is ready" in expired.stderr

    stale["expires_at"] = meta["expires_at"]
    (dest / "lease.json").write_text(json.dumps(stale))
    other = "registry-key-" + uuid.uuid4().hex
    add_cursor(box_env, root, "acct-a", other)
    won = seat(box_env, "run", "--vendor", "cursor", "--model", "grok-latest", "--cwd", str(root), "--", "task")
    assert won.returncode == 0, won.stderr
    assert saw.read_text() == other
    again = seat(box_env, "accounts", "--json")
    assert other not in again.stdout and key not in again.stdout
    kept = json.loads(again.stdout)["accounts"]
    assert len(kept) == 1 and kept[0]["id"] == "acct-a" and kept[0]["auth"] == "api-key"


def test_secret_containment(home: tuple) -> None:
    env, root, key, devin_key = home
    add_cursor(env, root, "acct-a", key)
    add_devin(env, root, devin_key)
    results = []
    metas = []
    limited = root / "limit.log"
    limited.write_text("transcript\nError: You've hit your usage limit. Try again in 2 hours. " + key)
    auth = root / "auth.log"
    auth.write_text("Error: invalid api key " + key)
    devin_log = root / "devin.log"
    devin_log.write_text("Error: not logged in " + devin_key)
    for vendor, log, flags in (
        ("cursor", limited, ()),
        ("cursor", auth, ("--account", "acct-a")),
        ("devin", devin_log, ()),
    ):
        out = root / uuid.uuid4().hex
        cmd = (
            "lease",
            "--vendor",
            vendor,
            "--for",
            "unit-1",
            "--ttl",
            "60",
            "--out",
            str(out),
            *(("--trust", "/w/one") if vendor == "devin" else ()),
            *flags,
            "--json",
        )
        issued = seat(env, *cmd)
        assert issued.returncode == 0, issued.stderr
        results.append(issued)
        meta = json.loads(issued.stdout)
        metas.append(out / "lease.json")
        if log == auth:
            (root / "revoked").write_text(key)
        if vendor == "devin":
            (root / "devin-revoked").touch()
        released = seat(env, "release", meta["lease"], "--log", str(log), "--json")
        assert released.returncode == 0, released.stderr
        results.append(released)
    visible = (
        "".join(r.stdout + r.stderr for r in results) + json.dumps(records(root)) + (root / "ledger.jsonl").read_text()
    )
    visible += "".join(p.read_text() for p in metas)
    assert key not in visible and devin_key not in visible
    for record in records(root)["accounts"].values():
        error = record.get("last_error")
        if error:
            assert error["text"] in (
                "limit in seat log: usage limit; Try again in 2 hour",
                "auth in seat log: invalid api key",
                "auth in seat log: not logged in",
            )
        assert record.get("auth_error") in (None, "rejected by probe after a leased run")
