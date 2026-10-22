from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from seat_storage import (  # pyrefly: ignore[missing-import]
    PROXY_VARS,
    expand,
    private_dir,
    read_json,
    seat_home,
)


def read_cursor_key(account: dict[str, Any]) -> str:
    try:
        key = expand(account.get("key_file")).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, AttributeError):
        raise RuntimeError(f"key file missing for {account['id']}") from None
    if not key:
        raise RuntimeError(f"key file empty for {account['id']}")
    return key


def devin_data_dir(account: dict[str, Any]) -> Path:
    if account.get("auth") == "lease":
        return Path(account["lease_dir"]) / "data"
    return expand(account.get("data_dir")) or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def read_lease_cursor_env(account: dict[str, Any]) -> dict[str, str]:
    path = Path(account["lease_dir"]) / "env"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise RuntimeError(f"key file missing for {account['id']}") from None
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if key not in ("CURSOR_API_KEY", "AGENT_CLI_CREDENTIAL_STORE"):
            continue
        try:
            values = shlex.split(raw, posix=True)
        except ValueError:
            continue
        if len(values) == 1:
            parsed[key] = values[0]
    if not parsed.get("CURSOR_API_KEY"):
        raise RuntimeError(f"key file empty for {account['id']}")
    return parsed


def local_proxy_vars(env: dict[str, str]) -> list[str]:
    removed = []
    for key in PROXY_VARS:
        value = env.get(key, "")
        try:
            host = urlsplit(value if "://" in value else "//" + value).hostname
        except ValueError:
            continue
        if host in ("127.0.0.1", "localhost"):
            removed.append(key)
    return removed


def account_env(account: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    vendor, auth = account.get("vendor"), account.get("auth")
    for key in local_proxy_vars(env):
        env.pop(key, None)
    if vendor == "cursor" and auth == "api-key":
        env["CURSOR_API_KEY"] = read_cursor_key(account)
        config_dir = seat_home() / "cursor" / account["id"]
        config_dir.mkdir(parents=True, exist_ok=True)
        env["CURSOR_CONFIG_DIR"] = str(config_dir)
        env["AGENT_CLI_CREDENTIAL_STORE"] = "memory"
    elif vendor == "cursor" and auth == "lease":
        env.update(read_lease_cursor_env(account))
        config_dir = Path(account["lease_dir"]) / "cursor-config"
        private_dir(config_dir)
        env["CURSOR_CONFIG_DIR"] = str(config_dir)
    elif vendor == "cursor":
        env.pop("CURSOR_API_KEY", None)
        env.pop("AGENT_CLI_CREDENTIAL_STORE", None)
    elif vendor == "muse":
        env.pop("META_API_KEY", None)
        env["MUSE_NO_AUTO_UPDATE"] = "1"
    elif vendor == "devin" and (auth == "lease" or expand(account.get("data_dir"))):
        env["XDG_DATA_HOME"] = str(devin_data_dir(account))
    return env


def devin_trusted(account: dict[str, Any], cwd: Path) -> bool:
    data_dir = devin_data_dir(account)
    trusted = read_json(data_dir / "devin" / "cli" / "trusted_workspaces.json", {})
    paths = trusted.get("trusted_paths") if isinstance(trusted, dict) else None
    resolved = cwd.resolve()
    for entry in paths or []:
        try:
            root = Path(entry).resolve()
        except (OSError, TypeError):
            continue
        if resolved == root or root in resolved.parents:
            return True
    return False
