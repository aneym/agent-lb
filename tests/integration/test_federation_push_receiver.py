"""Scenario tests for the federation push RECEIVER (openspec add-federation-account-push).

A peer LB (the owner and only refresher) pushes access tokens for some of its
accounts to POST /api/federation/push. The receiver authorizes the caller by
Tailscale identity, binds the source to one sender instance, upserts mirrored
rows owned by that instance, deactivates that instance's rows the push left
out, and answers with local usage for the accepted ids. Tests go through the
fixed seams only: the HTTP route, push_auth.resolve_tailscale_login (patched,
or driven through a fake tailscale binary), push_auth's source helpers,
push_receiver.load_binding, and the database.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import stat
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
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy.account_cache import get_account_selection_cache

pytestmark = pytest.mark.integration

_LOCAL_INSTANCE_ID = "studio-test"
_SENDER_INSTANCE_ID = "nate-lb"
_PULL_PEER_INSTANCE_ID = "laptop"
_MIRROR_TOKEN = "mirror-bearer-token"
_NATE_LOGIN = "nate@example.com"
_NATE_IP = "100.64.0.7"
_STRANGER_IP = "100.64.0.8"
_PUSH = "/api/federation/push"
_HOUR_MS = 3_600_000


# --------------------------------------------------------------------------- helpers


def _push_auth() -> ModuleType:
    # Imported lazily so the file collects while the receiver is still being written.
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
    # Guard against a coarse-mtime filesystem hiding a rewrite from the reload check.
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
    """(IP -> login table, lookup log) behind a patched push_auth.resolve_tailscale_login."""
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


# --------------------------------------------------------------------------- authorization


@pytest.mark.asyncio
async def test_listed_login_may_push_case_insensitively(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])

    response = await _push(app_instance, _push_body(_account("x")))

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "nate"
    assert response.json()["accepted"] == ["x"]


@pytest.mark.asyncio
async def test_callers_the_receiver_cannot_place_are_refused(
    async_client, app_instance, sources_path: Path, whois_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    table, calls = whois_calls
    _write_sources(sources_path, [_nate_source()])
    before = await _snapshot_accounts()

    # Loopback socket peer (the default test client), even though its login would match.
    table["127.0.0.1"] = _NATE_LOGIN
    loopback = await async_client.post(_PUSH, json=_push_body(_account("x")))
    assert loopback.status_code == 403

    # Tailnet IP whose login is not listed.
    stranger = await _push(app_instance, _push_body(_account("x")), ip=_STRANGER_IP)
    assert stranger.status_code == 403

    # Tailnet IP whose whois fails.
    unknown = await _push(app_instance, _push_body(_account("x")), ip="100.64.0.99")
    assert unknown.status_code == 403

    # Client IP that cannot be resolved: a trusted proxy forwarded a malformed chain.
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    get_settings.cache_clear()
    unresolved = await async_client.post(
        _PUSH, json=_push_body(_account("x")), headers={"X-Forwarded-For": "not-an-ip"}
    )
    assert unresolved.status_code == 403

    assert "127.0.0.1" not in calls
    assert await _snapshot_accounts() == before


@pytest.mark.asyncio
async def test_identity_headers_are_ignored(async_client, app_instance, sources_path: Path, whois_calls) -> None:
    _write_sources(sources_path, [_nate_source()])

    response = await _push(
        app_instance,
        _push_body(_account("x")),
        ip=_STRANGER_IP,
        headers={"Tailscale-User-Login": _NATE_LOGIN, "Tailscale-User-Name": "Nate"},
    )

    assert response.status_code == 403
    assert await _get_account("x") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [None, "{not json"], ids=["missing", "malformed"])
async def test_missing_or_malformed_sources_file_refuses_everyone(
    async_client, app_instance, sources_path: Path, whois_calls, contents: str | None
) -> None:
    if contents is not None:
        sources_path.write_text(contents)

    response = await _push(app_instance, _push_body(_account("x")))

    assert response.status_code == 403
    assert await _get_account("x") is None


@pytest.mark.asyncio
async def test_real_client_ip_behind_tailscale_serve_is_used(
    async_client, app_instance, sources_path: Path, whois_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, calls = whois_calls
    _write_sources(sources_path, [_nate_source()])
    forwarded = {"X-Forwarded-For": _NATE_IP}

    # Without proxy trust, the socket peer 127.0.0.1 is the client: refused as loopback.
    untrusted = await _push(app_instance, _push_body(_account("x")), ip="127.0.0.1", headers=forwarded)
    assert untrusted.status_code == 403
    assert calls == []

    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    get_settings.cache_clear()
    trusted = await _push(app_instance, _push_body(_account("x")), ip="127.0.0.1", headers=forwarded)

    assert trusted.status_code == 200, trusted.text
    assert calls == [_NATE_IP]


@pytest.mark.asyncio
async def test_team_mode_and_proxy_keys_do_not_gate_the_push(
    async_client, app_instance, sources_path: Path, whois_calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", "disabled")
    get_settings.cache_clear()
    enabled = await async_client.put("/api/settings", json={"teamModeEnabled": True, "apiKeyAuthEnabled": True})
    assert enabled.status_code == 200, enabled.text
    _write_sources(sources_path, [_nate_source()])

    async with _peer(app_instance, _NATE_IP) as peer:
        # The dashboard gate does refuse this peer, so the 200 below is the push route's own auth.
        gated = await peer.get("/api/accounts")
        assert gated.status_code in (401, 403), gated.text
        pushed = await peer.post(_PUSH, json=_push_body(_account("x")))

    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["accepted"] == ["x"]


@pytest.mark.asyncio
async def test_sources_file_edits_apply_without_restart(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [{"name": "someone", "tailscale_logins": ["someone@example.com"]}])
    refused = await _push(app_instance, _push_body(_account("x")))
    assert refused.status_code == 403

    _write_sources(sources_path, [_nate_source()])
    _bump_mtime(sources_path)
    accepted = await _push(app_instance, _push_body(_account("x")))

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["source"] == "nate"


# --------------------------------------------------------------------------- binding


@pytest.mark.asyncio
async def test_first_push_binds_source_with_private_state_file(
    async_client, app_instance, sources_path: Path, state_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])

    response = await _push(app_instance, _push_body(_account("x")))

    assert response.status_code == 200, response.text
    assert _push_receiver().load_binding(state_path) == {"nate": _SENDER_INSTANCE_ID}
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_second_instance_cannot_take_over_bound_source(
    async_client, app_instance, sources_path: Path, state_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])
    first = await _push(app_instance, _push_body(_account("x"), _account("y")))
    assert first.status_code == 200, first.text
    before = await _snapshot_accounts()

    # A wrongful accept would add row z.
    takeover = await _push(app_instance, _push_body(_account("z"), instance_id="other-lb"))

    assert takeover.status_code == 409
    assert await _snapshot_accounts() == before
    assert _push_receiver().load_binding(state_path) == {"nate": _SENDER_INSTANCE_ID}


@pytest.mark.asyncio
async def test_push_cannot_claim_own_or_pull_peer_instance_id(
    async_client, app_instance, sources_path: Path, state_path: Path, whois_calls
) -> None:
    await _seed_account("z", owner_instance=_PULL_PEER_INSTANCE_ID, access_token="laptop-access")
    await _seed_account("o", owner_instance=_LOCAL_INSTANCE_ID, access_token="local-access")
    _write_sources(sources_path, [_nate_source()])
    before = await _snapshot_accounts()

    as_local = await _push(app_instance, _push_body(_account("x"), instance_id=_LOCAL_INSTANCE_ID))
    # An empty push as the pull peer would otherwise deactivate every laptop row.
    as_pull_peer = await _push(app_instance, _push_body(instance_id=_PULL_PEER_INSTANCE_ID))

    assert as_local.status_code == 409
    assert as_pull_peer.status_code == 409
    assert await _snapshot_accounts() == before
    assert _push_receiver().load_binding(state_path) == {}


# --------------------------------------------------------------------------- apply


@pytest.mark.asyncio
async def test_new_accounts_become_mirrored_rows_owned_by_sender(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])

    response = await _push(app_instance, _push_body(_account("x"), _account("y")))

    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(body["accepted"]) == ["x", "y"]
    assert body["skipped"] == []
    assert body["removed"] == []
    for account_id in ("x", "y"):
        row = await _get_account(account_id)
        assert row is not None
        assert row.owner_instance == _SENDER_INSTANCE_ID
        assert row.status == AccountStatus.ACTIVE
        assert _decrypt(row.access_token_encrypted) == f"pushed-access-{account_id}"
        assert _decrypt(row.refresh_token_encrypted) == ""


@pytest.mark.asyncio
async def test_locally_owned_and_foreign_owned_ids_are_not_overwritten(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    await _seed_account("a", owner_instance=_LOCAL_INSTANCE_ID, access_token="local-a")
    await _seed_account("n", owner_instance=None, access_token="local-n")
    await _seed_account("b", owner_instance=_PULL_PEER_INSTANCE_ID, access_token="laptop-b")
    _write_sources(sources_path, [_nate_source()])
    before = await _snapshot_accounts()

    response = await _push(app_instance, _push_body(_account("a"), _account("n"), _account("b")))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == []
    assert {(skip["account_id"], skip["reason"]) for skip in body["skipped"]} == {
        ("a", "conflict_owned_elsewhere"),
        ("n", "conflict_owned_elsewhere"),
        ("b", "conflict_owned_elsewhere"),
    }
    assert await _snapshot_accounts() == before


@pytest.mark.asyncio
async def test_unrouted_expired_and_overflow_accounts_are_skipped(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source(max_accounts=2)])

    response = await _push(
        app_instance,
        _push_body(
            _account("p", provider="made-up"),
            _account("e", expires_in_ms=-_HOUR_MS),
            _account("l1"),
            _account("l2"),
            _account("l3"),
        ),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == ["l1", "l2"]
    assert [(skip["account_id"], skip["reason"]) for skip in body["skipped"]] == [
        ("p", "unsupported_provider"),
        ("e", "expired_token"),
        ("l3", "over_limit"),
    ]
    for skipped_id in ("p", "e", "l3"):
        assert await _get_account(skipped_id) is None


@pytest.mark.asyncio
async def test_dropped_accounts_are_deactivated_scoped_to_that_owner_and_come_back_on_repush(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    await _seed_account("z", owner_instance=_PULL_PEER_INSTANCE_ID, access_token="laptop-z")
    await _seed_account("o", owner_instance=_LOCAL_INSTANCE_ID, access_token="local-o")
    await _seed_account("n", owner_instance=None, access_token="local-n")
    _write_sources(sources_path, [_nate_source()])
    first = await _push(app_instance, _push_body(_account("x"), _account("y")))
    assert first.status_code == 200, first.text
    others = {"z", "o", "n"}
    others_before = {k: v for k, v in (await _snapshot_accounts()).items() if k in others}

    # Owner drops y.
    second = await _push(app_instance, _push_body(_account("x", token="pushed-access-x-2")))
    assert second.status_code == 200, second.text
    assert second.json()["accepted"] == ["x"]
    assert second.json()["removed"] == ["y"]
    x_row = await _get_account("x")
    y_row = await _get_account("y")
    assert x_row is not None and x_row.status == AccountStatus.ACTIVE
    assert _decrypt(x_row.access_token_encrypted) == "pushed-access-x-2"
    assert y_row is not None
    assert y_row.status == AccountStatus.DEACTIVATED
    assert y_row.owner_instance == _SENDER_INSTANCE_ID
    assert _decrypt(y_row.access_token_encrypted) != "pushed-access-y"

    # Owner pushes y again: it is live again with the new token.
    third = await _push(app_instance, _push_body(_account("x"), _account("y", token="pushed-access-y-2")))
    assert third.status_code == 200, third.text
    assert sorted(third.json()["accepted"]) == ["x", "y"]
    y_row = await _get_account("y")
    assert y_row is not None and y_row.status == AccountStatus.ACTIVE
    assert _decrypt(y_row.access_token_encrypted) == "pushed-access-y-2"

    # An empty push deactivates everything this owner pushed, and nothing else.
    empty = await _push(app_instance, _push_body())
    assert empty.status_code == 200, empty.text
    assert sorted(empty.json()["removed"]) == ["x", "y"]
    for account_id in ("x", "y"):
        row = await _get_account(account_id)
        assert row is not None and row.status == AccountStatus.DEACTIVATED
        assert _decrypt(row.access_token_encrypted) != "pushed-access-x"

    assert {k: v for k, v in (await _snapshot_accounts()).items() if k in others} == others_before


@pytest.mark.asyncio
async def test_changes_invalidate_the_selection_cache(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    _write_sources(sources_path, [_nate_source()])
    cache = get_account_selection_cache()

    generation = cache.generation
    upserted = await _push(app_instance, _push_body(_account("x")))
    assert upserted.status_code == 200, upserted.text
    assert cache.generation > generation

    generation = cache.generation
    removed = await _push(app_instance, _push_body())
    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] == ["x"]
    assert cache.generation > generation


# --------------------------------------------------------------------------- response content


@pytest.mark.asyncio
async def test_usage_is_filtered_to_accepted_accounts(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    await _seed_account("o", owner_instance=_LOCAL_INSTANCE_ID)
    _write_sources(sources_path, [_nate_source()])
    # request_logs.account_id references accounts, so x must exist before it has usage.
    first = await _push(app_instance, _push_body(_account("x")))
    assert first.status_code == 200, first.text
    now = utcnow()
    async with SessionLocal() as session:
        for index, account_id in enumerate(("x", "o", "x")):
            session.add(
                RequestLog(
                    account_id=account_id,
                    provider="anthropic",
                    session_id=f"session-{index}",
                    request_id=f"request-{index}",
                    requested_at=now,
                    model="claude-test",
                    input_tokens=10,
                    output_tokens=2,
                    cost_usd=0.1,
                    status="success",
                )
            )
        await session.commit()

    response = await _push(app_instance, _push_body(_account("x")))

    assert response.status_code == 200, response.text
    usage = response.json()["usage"]
    assert {row["account_id"] for row in usage} == {"x"}
    assert sum(row["requests"] for row in usage) == 2


@pytest.mark.asyncio
async def test_tokens_never_leave_the_receiver(
    async_client, app_instance, sources_path: Path, whois_calls, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await _seed_account("a", owner_instance=_LOCAL_INSTANCE_ID, access_token="local-a", refresh_token="local-refresh-a")
    _write_sources(sources_path, [_nate_source(max_accounts=1)])
    pushed_tokens = ["secret-live-token", "secret-expired-token", "secret-over-token", "secret-conflict-token"]
    secrets = [*pushed_tokens, "local-a", "local-refresh-a"]

    accepted = await _push(
        app_instance,
        _push_body(
            _account("x", token=pushed_tokens[0]),
            _account("e", token=pushed_tokens[1], expires_in_ms=-_HOUR_MS),
            _account("l", token=pushed_tokens[2]),
            _account("a", token=pushed_tokens[3]),
        ),
    )
    refused = await _push(app_instance, _push_body(_account("x", token=pushed_tokens[0])), ip=_STRANGER_IP)
    conflict = await _push(app_instance, _push_body(_account("x", token=pushed_tokens[0]), instance_id="other-lb"))

    assert (accepted.status_code, refused.status_code, conflict.status_code) == (200, 403, 409)
    assert {skip["reason"] for skip in accepted.json()["skipped"]} == {
        "expired_token",
        "over_limit",
        "conflict_owned_elsewhere",
    }
    for response in (accepted, refused, conflict):
        for secret in secrets:
            assert secret not in response.text
    for secret in secrets:
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_mirror_export_is_unchanged_and_skips_pushed_rows(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    await _seed_account("o", owner_instance=_LOCAL_INSTANCE_ID, access_token="local-o")
    mirror_headers = {"Authorization": f"Bearer {_MIRROR_TOKEN}"}
    before = await async_client.get("/api/federation/mirror", headers=mirror_headers)
    assert before.status_code == 200, before.text
    _write_sources(sources_path, [_nate_source()])

    pushed = await _push(app_instance, _push_body(_account("x")))
    assert pushed.status_code == 200, pushed.text
    after = await async_client.get("/api/federation/mirror", headers=mirror_headers)

    assert after.status_code == 200, after.text
    assert after.json() == before.json()
    assert [account["account_id"] for account in after.json()["accounts"]] == ["o"]
    assert "pushed-access-x" not in after.text


# --------------------------------------------------------------------------- push_auth helpers


@pytest.fixture
def fake_tailscale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A fake tailscale binary wired through settings.federation_tailscale_bin.

    Returns (call log, "healed" flag file). 100.64.77.201 answers with a leading
    warning line; .202 fails until the flag file exists; .203 prints non-JSON.
    These IPs are used nowhere else, so the module's per-IP cache cannot leak in.
    """
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


@pytest.mark.asyncio
async def test_whois_parses_json_after_a_leading_warning_line_and_caches_per_ip(fake_tailscale) -> None:
    log, _ = fake_tailscale
    push_auth = _push_auth()

    first = await push_auth.resolve_tailscale_login("100.64.77.201")
    second = await push_auth.resolve_tailscale_login("100.64.77.201")

    assert first == "Nate@Example.com"
    assert second == "Nate@Example.com"
    assert _whois_count(log, "100.64.77.201") == 1


@pytest.mark.asyncio
async def test_whois_failures_return_none_and_are_not_cached(fake_tailscale) -> None:
    log, healed = fake_tailscale
    push_auth = _push_auth()

    assert await push_auth.resolve_tailscale_login("100.64.77.203") is None
    assert await push_auth.resolve_tailscale_login("100.64.77.202") is None
    healed.touch()
    assert await push_auth.resolve_tailscale_login("100.64.77.202") == "Nate@Example.com"
    assert _whois_count(log, "100.64.77.202") == 2


def test_sources_file_shape_and_case_insensitive_match(tmp_path: Path) -> None:
    push_auth = _push_auth()
    path = tmp_path / "federation-push-sources.json"
    assert push_auth.load_push_sources(path) == []

    _write_sources(path, [{"name": "nate", "tailscale_logins": ["Nate@Example.com", "n2@example.com"]}])
    sources = push_auth.load_push_sources(path)

    assert sources == [
        push_auth.PushSource(name="nate", tailscale_logins=("Nate@Example.com", "n2@example.com"), max_accounts=25)
    ]
    matched = push_auth.match_source("NATE@example.COM", sources)
    assert matched is not None and matched.name == "nate"
    assert push_auth.match_source("other@example.com", sources) is None
    assert push_auth.match_source(None, sources) is None
