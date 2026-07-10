#!/usr/bin/env python
"""DB helper for the two-instance federation exercise.

Subcommands operate directly on a per-instance sqlite file with that
instance's own encryption key. Never touches real credentials.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet


def _enc(keyfile: str, token: str) -> bytes:
    return Fernet(Path(keyfile).read_bytes()).encrypt(token.encode())


def _dec(keyfile: str, blob: bytes) -> str:
    return Fernet(Path(keyfile).read_bytes()).decrypt(blob).decode()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def mkjwt(exp_offset_seconds: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
    exp = int(time.time()) + exp_offset_seconds
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp, "email": "fed-test@example.invalid"}).encode()).rstrip(
        b"="
    )
    return f"{header.decode()}.{payload.decode()}.sig"


def seed(db_path: str, keyfile: str, account_id: str, access: str, refresh: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    conn = _connect(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO accounts
                (id, provider, email, alias, plan_type,
                 access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                 last_refresh, created_at, status, owner_instance)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'active', NULL)
            """,
            (
                account_id,
                "anthropic",
                "fed-test@example.invalid",
                "fed-test",
                "pro",
                _enc(keyfile, access),
                _enc(keyfile, refresh),
                now,
                now,
            ),
        )
    conn.close()


def rotate(db_path: str, keyfile: str, account_id: str, access: str, refresh: str) -> None:
    conn = _connect(db_path)
    with conn:
        n = conn.execute(
            "UPDATE accounts SET access_token_encrypted=?, refresh_token_encrypted=? WHERE id=?",
            (_enc(keyfile, access), _enc(keyfile, refresh), account_id),
        ).rowcount
    conn.close()
    if n != 1:
        raise SystemExit(f"rotate updated {n} rows (expected 1)")


def owner(db_path: str, account_id: str) -> None:
    conn = _connect(db_path)
    row = conn.execute("SELECT owner_instance FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    if row is None:
        print("MISSING")
    else:
        print("NULL" if row[0] is None else row[0])


def exists(db_path: str, account_id: str) -> None:
    conn = _connect(db_path)
    row = conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    print("1" if row else "0")


def decrypt(db_path: str, keyfile: str, account_id: str) -> None:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT access_token_encrypted, refresh_token_encrypted FROM accounts WHERE id=?",
        (account_id,),
    ).fetchone()
    conn.close()
    if row is None:
        print("MISSING\tMISSING")
        return
    print(f"{_dec(keyfile, row[0])}\t{_dec(keyfile, row[1])}")


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "mkjwt":
        print(mkjwt(int(sys.argv[2])))
    elif cmd == "seed":
        seed(*sys.argv[2:])
    elif cmd == "rotate":
        rotate(*sys.argv[2:])
    elif cmd == "owner":
        owner(*sys.argv[2:])
    elif cmd == "exists":
        exists(*sys.argv[2:])
    elif cmd == "decrypt":
        decrypt(*sys.argv[2:])
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
