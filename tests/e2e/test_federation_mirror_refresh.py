from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

_TEST_BEARER = {"Authorization": "Bearer mirror-refresh-fixture"}


@contextmanager
def _instance(home: Path, listener: socket.socket, *, peer: str = "", nudge_url: str = ""):
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    home.mkdir()
    key = Fernet.generate_key()
    key_path = home / "fixture.key"
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    database = home / "fixture.db"
    env = {
        **os.environ,
        "AGENT_LB_DATA_DIR": str(home),
        "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(key_path),
        "AGENT_LB_LOCAL_INSTANCE_ID": home.name,
        "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
        "AGENT_LB_ACCOUNT_PULSE_ENABLED": "false",
        "AGENT_LB_FEDERATION_MIRROR_TOKEN": "mirror-refresh-fixture",
        "AGENT_LB_FEDERATION_PEER_URL": peer,
        "AGENT_LB_FEDERATION_MIRROR_NUDGE_URL": nudge_url,
        "AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS": "300",
        "AGENT_LB_FEDERATION_MIRROR_INCLUDE_PUSHED": "true",
    }
    with (
        (home / "server.log").open("w") as log,
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--fd", str(listener.fileno())],
            env=env,
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=log,
        ) as server,
    ):
        try:
            with (
                httpx.Client(base_url=origin, timeout=2, trust_env=False) as client,
                sqlite3.connect(database, autocommit=True) as db,
            ):
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline and server.poll() is None:
                    try:
                        if client.get("/health/ready").is_success:
                            break
                    except (httpx.ConnectError, httpx.ReadTimeout):
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail(f"{home.name} service did not start: {home / 'server.log'}")
                yield client, db, Fernet(key), env
        finally:
            server.terminate()
            server.wait(timeout=15)


def _seed(db: sqlite3.Connection, crypto: Fernet, account_id: str, *, owner=None, refresh=b"", **fields):
    now = datetime.now(UTC).replace(tzinfo=None)
    values = {
        "id": account_id,
        "email": f"{account_id}@example.invalid",
        "provider": "anthropic",
        "plan_type": "claude",
        "access_token_encrypted": crypto.encrypt(b"fixture-access"),
        "refresh_token_encrypted": crypto.encrypt(refresh),
        "status": "active",
        "last_refresh": now.isoformat(sep=" "),
        "owner_instance": owner,
        **fields,
    }
    placeholders = ", ".join("?" for _ in values)
    db.execute(f"INSERT INTO accounts ({', '.join(values)}) VALUES ({placeholders})", tuple(values.values()))


def _wait_for_accounts(edge: httpx.Client, *, includes: set[str], excludes: set[str], deadline: float) -> dict:
    while time.monotonic() < deadline:
        reply = edge.get("/api/accounts", params={"fresh": True})
        reply.raise_for_status()
        accounts = {account["accountId"]: account for account in reply.json()["accounts"]}
        if includes <= accounts.keys() and not (excludes & accounts.keys()):
            return accounts
        time.sleep(0.025)
    pytest.fail(f"Edge did not refresh before deadline; wanted {includes}, removed {excludes}")


@pytest.mark.e2e
def test_mirror_nudge_prunes_deleted_accounts_without_losing_custody(tmp_path):
    with socket.socket() as primary_socket, socket.socket() as edge_socket:
        primary_socket.bind(("127.0.0.1", 0))
        edge_socket.bind(("127.0.0.1", 0))
        primary_origin = f"http://127.0.0.1:{primary_socket.getsockname()[1]}"
        edge_origin = f"http://127.0.0.1:{edge_socket.getsockname()[1]}"
        with _instance(tmp_path / "primary", primary_socket, nudge_url=edge_origin) as primary_resources:
            primary, primary_db, primary_crypto, primary_env = primary_resources
            expires = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
            _seed(primary_db, primary_crypto, "stale", owner="pusher", access_expires_at=expires.isoformat(sep=" "))
            _seed(
                primary_db,
                primary_crypto,
                "recovering",
                access_expires_at=expires.isoformat(sep=" "),
                status="reauth_required",
                subscription_status="paused",
            )
            _seed(primary_db, primary_crypto, "deleted")
            _seed(primary_db, primary_crypto, "warm")
            snapshot = primary.get("/api/federation/mirror", headers=_TEST_BEARER)
            snapshot.raise_for_status()
            assert {row["account_id"] for row in snapshot.json()["accounts"] if row["owner_stale"]} == {"stale"}

            with _instance(tmp_path / "edge", edge_socket, peer=primary_origin) as edge_resources:
                edge, edge_db, edge_crypto, _ = edge_resources
                before = _wait_for_accounts(
                    edge,
                    includes={"deleted", "warm", "stale", "recovering"},
                    excludes=set(),
                    deadline=time.monotonic() + 3,
                )
                assert before["recovering"]["ownerStale"] is True
                assert before["recovering"]["status"] == "reauth_required"
                _seed(edge_db, edge_crypto, "local", owner="edge", refresh=b"local-fixture-refresh")
                _seed(edge_db, edge_crypto, "leased", owner="primary")
                _seed(edge_db, edge_crypto, "foreign", owner="another-peer")
                _seed(edge_db, edge_crypto, "refresh-custody", owner="primary", refresh=b"custody-fixture-refresh")
                edge_db.execute(
                    "INSERT INTO account_transfers (id, account_id, nonce, direction, counterparty_instance_id, state) "
                    "VALUES ('lease', 'leased', 'fixture-nonce', 'checkout', 'primary', 'settled')"
                )
                warm_until = (expires + timedelta(hours=3)).isoformat(sep=" ")
                primary_db.execute("UPDATE accounts SET warmup_until = ? WHERE id = 'warm'", (warm_until,))
                primary_db.execute(
                    "UPDATE accounts SET status = 'active', subscription_status = 'active', access_expires_at = ? "
                    "WHERE id = 'recovering'",
                    (warm_until,),
                )
                primary.delete("/api/accounts/deleted").raise_for_status()
                assert edge.post("/api/federation/mirror/nudge").status_code == 403
                assert (
                    edge.post(
                        "/api/federation/mirror/nudge", headers={"Authorization": "Bearer not-the-test-bearer"}
                    ).status_code
                    == 403
                )
                assert edge_db.execute("SELECT id FROM accounts WHERE id = 'deleted'").fetchone() is not None
                nudge_started = time.monotonic()
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import asyncio; from app.modules.federation.nudge import nudge_federation_mirror; "
                        "raise SystemExit(not asyncio.run(nudge_federation_mirror()))",
                    ],
                    env=primary_env,
                    check=True,
                    timeout=5,
                )
                accounts = _wait_for_accounts(
                    edge,
                    includes={"warm", "stale", "local", "leased", "foreign", "refresh-custody", "recovering"},
                    excludes={"deleted"},
                    deadline=nudge_started + 1,
                )
                assert accounts["stale"]["ownerStale"] is True
                assert accounts["local"]["ownerStale"] is False
                assert accounts["recovering"]["ownerStale"] is False
                assert accounts["recovering"]["status"] == "active"
                assert accounts["recovering"]["subscription"]["status"] == "active"
                status = edge.get("/api/federation/status")
                status.raise_for_status()
                assert status.json()["accounts"]["ownerStale"] == 1
