from __future__ import annotations
import base64
import datetime as dt
import hashlib
import os
import sqlite3
import stat
import time
from lb_sandbox_credentials import (
    check_data_custody,
    entry_of,
    live_federation_token,
    open_data_dir,
    read_key,
    store_rows,
)
from lb_sandbox_paths import (
    Refused,
    USABLE_EXPIRY_S,
    Unhealthy,
    VENDOR_OF_PROVIDER,
)
from lb_sandbox_protection import (
    run_confined,
)


def _fernet(root: Path):
    from cryptography.fernet import Fernet

    key = read_key(root)
    if not key:
        raise Refused("sandbox store key missing")
    return Fernet(key.encode())


def read_accounts(root: Path, *, with_tokens: bool = False, strict: bool = False) -> list[dict]:

    try:
        check_data_custody(root)
        data_fd = open_data_dir(root)
    except FileNotFoundError:
        data_fd = None
    except (Refused, OSError):
        if strict:
            raise Refused("sandbox data dir is linked, shared or not the root's; its tokens cannot be read") from None
        return []
    has_store = False
    if data_fd is not None:
        try:
            has_store = stat.S_ISREG(os.stat("store.db", dir_fd=data_fd, follow_symlinks=False).st_mode)
        except OSError:
            pass
        finally:
            os.close(data_fd)
    if not has_store:
        if strict:
            raise Refused("sandbox store missing; its account tokens cannot be scanned for")
        return []
    try:

        rows = run_confined(root, lambda: store_rows(root), reads=True, timeout_s=60)
    except (Refused, Unhealthy, sqlite3.Error):
        if strict:
            raise
        return []
    rows = [tuple(base64.b64decode(v["b64"]) if isinstance(v, dict) else v for v in row) for row in rows]
    fernet = _fernet(root) if rows else None
    now = time.time()
    accounts = []

    def decrypt(blob) -> str:
        try:
            return fernet.decrypt(bytes(blob)).decode() if fernet and blob else ""
        except Exception:
            if strict:
                raise
            return ""

    for account_id, provider, status, expires_at, access_enc, id_enc, reset_at, blocked_at, refresh_enc in rows:
        token = decrypt(access_enc)
        expires_in = None
        if expires_at:
            try:
                stamp = dt.datetime.fromisoformat(str(expires_at)).replace(tzinfo=dt.timezone.utc)
                expires_in = int(stamp.timestamp() - now)
            except ValueError:
                expires_in = None
        cooling = status in ("rate_limited", "quota_exceeded") or bool(
            blocked_at and reset_at and float(reset_at) > now
        )
        entry = {
            "id": account_id,
            "vendor": VENDOR_OF_PROVIDER.get(str(provider), str(provider)),
            "status": status,
            "expires_in_s": expires_in,
            "auth_hash12": hashlib.sha256(f"Bearer {token}".encode()).hexdigest()[:12] if token else None,
            "in_cooldown": cooling,
            "cooldown_until": int(reset_at) if cooling and reset_at else None,
            "usable": status == "active" and expires_in is not None and expires_in > USABLE_EXPIRY_S,
        }
        if with_tokens:
            entry["_secrets"] = [secret for secret in (token, decrypt(id_enc), decrypt(refresh_enc)) if secret]

            entry["_ciphertexts"] = [
                bytes(blob).decode("ascii", "replace") for blob in (access_enc, id_enc, refresh_enc) if blob
            ]
        accounts.append(entry)
    return sorted(accounts, key=lambda a: (a["vendor"], a["id"]))


def load_secrets(root: Path | None) -> list[str]:

    secrets = []
    token = live_federation_token()
    if not token:
        raise Refused("live federation token not found; a scan without it would be incomplete")
    secrets.append(token)
    if root is not None and entry_of(root) == "dir":
        for account in read_accounts(root, with_tokens=True, strict=True):
            secrets.extend(s for s in account.pop("_secrets") if s)
            secrets.extend(s for s in account.pop("_ciphertexts") if s)
        key = load_key(root)
        if key:
            secrets.append(key)
    return secrets


def load_key(root: Path | None) -> str | None:

    if root is None or entry_of(root) != "dir":
        return None
    try:
        return read_key(root)
    except OSError as exc:
        raise Refused(f"sandbox store key is redirected or shared ({exc.__class__.__name__})") from None
