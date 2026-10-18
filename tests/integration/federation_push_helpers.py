from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

_LOCAL_INSTANCE_ID = "studio-test"


_SENDER_INSTANCE_ID = "nate-lb"


_PULL_PEER_INSTANCE_ID = "laptop"


_MIRROR_TOKEN = "mirror-bearer-token"


_NATE_LOGIN = "nate@example.com"


_NATE_IP = "100.64.0.7"


_STRANGER_IP = "100.64.0.8"


_PUSH = "/api/federation/push"


_HOUR_MS = 3_600_000


def _push_auth() -> ModuleType:

    return importlib.import_module("app.modules.federation.push_auth")


def _push_receiver() -> ModuleType:
    return importlib.import_module("app.modules.federation.push_receiver")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _account(
    account_id: str, *, token: str | None = None, provider: str = "anthropic", expires_in_ms: int = _HOUR_MS
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "provider": provider,
        "alias": f"{account_id}-alias",
        "email": f"{account_id}@nate.example",
        "status": "active",
        "plan_type": "claude",
        "chatgpt_account_id": None,
        "access_token": token or f"pushed-access-{account_id}",
        "expires_at_ms": _now_ms() + expires_in_ms,
    }


def _push_body(*accounts: dict[str, Any], instance_id: str = _SENDER_INSTANCE_ID) -> dict[str, Any]:
    return {"instance_id": instance_id, "accounts": list(accounts)}


def _write_sources(path: Path, sources: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"sources": sources}))


def _nate_source(max_accounts: int | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {"name": "nate", "tailscale_logins": [_NATE_LOGIN]}
    if max_accounts is not None:
        source["max_accounts"] = max_accounts
    return source


def _bump_mtime(path: Path) -> None:

    stamp = path.stat().st_mtime + 5
    os.utime(path, (stamp, stamp))


async def _seed_account(
    account_id: str,
    *,
    owner_instance: str | None,
    access_token: str = "seed-access",
    refresh_token: str = "seed-refresh",
) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider="anthropic",
        chatgpt_account_id=None,
        email=f"{account_id}@example.com",
        alias="seed-alias",
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt(access_token),
        refresh_token_encrypted=encryptor.encrypt(refresh_token),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner_instance
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()


async def _get_account(account_id: str) -> Account | None:
    async with SessionLocal() as session:
        return await session.get(Account, account_id)


async def _snapshot_accounts() -> dict[str, tuple[Any, ...]]:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        rows = (await session.execute(select(Account))).scalars().all()
    return {
        row.id: (
            row.owner_instance,
            row.status,
            encryptor.decrypt(row.access_token_encrypted),
            encryptor.decrypt(row.refresh_token_encrypted),
        )
        for row in rows
    }


def _decrypt(value: bytes) -> str:
    return TokenEncryptor().decrypt(value)


@pytest.fixture
def sources_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "federation-push-sources.json"
    monkeypatch.setenv("AGENT_LB_FEDERATION_PUSH_SOURCES_PATH", str(path))
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", _LOCAL_INSTANCE_ID)
    monkeypatch.setenv("AGENT_LB_FEDERATION_TOKEN", _MIRROR_TOKEN)
    get_settings.cache_clear()
    return path


@pytest.fixture
def state_path(sources_path: Path) -> Path:
    return sources_path.with_name("federation-push-state.json")


@pytest.fixture
def whois_calls(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, str], list[str]]:

    table: dict[str, str] = {_NATE_IP: "Nate@Example.com", _STRANGER_IP: "stranger@example.com"}
    calls: list[str] = []

    async def fake_resolve(ip: str) -> str | None:
        calls.append(ip)
        return table.get(ip)

    monkeypatch.setattr(_push_auth(), "resolve_tailscale_login", fake_resolve)
    return table, calls


def _peer(app_instance: Any, ip: str) -> AsyncClient:
    transport = ASGITransport(app=app_instance, client=(ip, 443))
    return AsyncClient(transport=transport, base_url="http://studio.tailnet")


async def _push(app_instance: Any, body: dict[str, Any], *, ip: str = _NATE_IP, headers: dict[str, str] | None = None):
    async with _peer(app_instance, ip) as peer:
        return await peer.post(_PUSH, json=body, headers=headers or {})


@pytest.fixture
def fake_tailscale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:

    log = tmp_path / "whois-calls.log"
    healed = tmp_path / "healed"
    script = tmp_path / "tailscale"
    profile = json.dumps({"Node": {"Name": "nate-mac"}, "UserProfile": {"LoginName": "Nate@Example.com"}})
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        '[ "$1" = "whois" ] && [ "$2" = "--json" ] || exit 2\n'
        'case "$3" in\n'
        "  100.64.77.201)\n"
        '    echo \'Warning: client version "1.80.0" != tailscaled server version "1.82.0"\'\n'
        f"    echo '{profile}' ;;\n"
        "  100.64.77.202)\n"
        f'    [ -f "{healed}" ] || exit 1\n'
        f"    echo '{profile}' ;;\n"
        "  100.64.77.203) echo 'no peer found' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAILSCALE_BIN", str(script))
    get_settings.cache_clear()
    return log, healed


def _whois_count(log: Path, ip: str) -> int:
    if not log.exists():
        return 0
    return sum(1 for line in log.read_text().splitlines() if line.split()[-1:] == [ip])
