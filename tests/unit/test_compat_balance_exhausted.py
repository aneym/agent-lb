from __future__ import annotations

import asyncio
import json
import runpy
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.anthropic_service import AnthropicProxyService


class _Response:
    def __init__(self, status: int, body: dict) -> None:
        self.status = status
        self.headers = {}
        self._body = json.dumps(body).encode()
        self.content = self

    async def read(self) -> bytes:
        return self._body

    async def iter_chunked(self, size):
        yield self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,model,status,error,code",
    [
        ("glm", "glm-5.2", 429, {"code": "1113", "message": "Insufficient balance"}, "1113"),
        ("glm", "glm-5.2", 400, {"code": 1113, "message": "Insufficient balance"}, "1113"),
        ("glm", "glm-5.2", 400, "Insufficient balance", "1113"),
        (
            "kimi",
            "kimi-k3",
            402,
            {"code": "unsafe token!" * 8, "message": "Account suspended"},
            ("unsafe_token_" * 8)[:40],
        ),
        ("kimi", "kimi-k3", 403, {"type": "account_suspended", "message": "Account suspended"}, "account_suspended"),
        (
            "kimi",
            "kimi-k3",
            429,
            {"type": "insufficient_balance", "message": "Insufficient balance"},
            "insufficient_balance",
        ),
    ],
)
async def test_anthropic_compat_balance_parks_until_reactivated(
    async_client, monkeypatch, provider, model, status, error, code
):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="compat-balance",
                provider=provider,
                chatgpt_account_id="compat-balance",
                email="compat@example.invalid",
                plan_type=f"{provider}-coding",
                access_token_encrypted=encryptor.encrypt("fixture-key"),
                refresh_token_encrypted=encryptor.encrypt("fixture-key"),
                last_refresh=utcnow() + timedelta(days=1),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()

    calls = []

    def upstream(self, session, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _Response(status, {"error": error, **({"code": 1113} if isinstance(error, str) else {})})
        return _Response(200, {"type": "message", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}})

    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", upstream)
    payload = {"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "hello"}]}
    first = await async_client.post("/v1/messages", json=payload)
    assert first.status_code == 503
    assert first.headers["x-should-retry"] == "false"
    assert first.json()["error"]["type"] == "balance_exhausted"
    async with SessionLocal() as session:
        account = await session.get(Account, "compat-balance")
        assert account.status == AccountStatus.PAUSED
        assert account.deactivation_reason == f"balance_exhausted: {code}"
        assert account.reset_at is None
        assert account.blocked_at is None

    second = await async_client.post("/v1/messages", json=payload)
    assert second.status_code == 503
    assert "No balance" in second.json()["error"]["message"]
    assert len(calls) == 1
    pools = await async_client.get("/api/pools")
    pool = next(pool for pool in pools.json()["pools"] if pool["id"] == provider)
    assert pool["unavailableReason"] == "no balance"
    assert pool["eligibleAccounts"] == 0
    assert pool["resetAt"] is None

    restored = await async_client.post("/api/accounts/compat-balance/reactivate")
    assert restored.status_code == 200
    third = await async_client.post("/v1/messages", json=payload)
    assert third.status_code == 200
    assert len(calls) == 2
    async with SessionLocal() as session:
        account = await session.get(Account, "compat-balance")
        assert account.status == AccountStatus.ACTIVE
        assert account.deactivation_reason is None


async def _insert_glm(account_id, key):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider="glm",
                chatgpt_account_id=account_id,
                email=f"{account_id}@example.invalid",
                plan_type="glm-coding",
                access_token_encrypted=encryptor.encrypt(key),
                refresh_token_encrypted=encryptor.encrypt(key),
                last_refresh=utcnow() + timedelta(days=1),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_anthropic_compat_concurrent_balance_failures_fail_over(async_client, monkeypatch):
    await _insert_glm("empty", "empty-key")
    await _insert_glm("healthy", "healthy-key")
    upstream_barrier = asyncio.Event()
    pause_barrier = asyncio.Event()
    rejected = 0
    snapshots = 0
    parks = []
    original_get = AccountsRepository.get_by_id
    original_update = AccountsRepository.update_status_if_current

    async def concurrent_snapshot(repo, account_id):
        nonlocal snapshots
        account = await original_get(repo, account_id)
        if account_id == "empty" and account.status == AccountStatus.ACTIVE:
            snapshots += 1
            if snapshots == 2:
                pause_barrier.set()
            await asyncio.wait_for(pause_barrier.wait(), 5)
        return account

    async def count_park(repo, account_id, status, *args, **kwargs):
        result = await original_update(repo, account_id, status, *args, **kwargs)
        if result and status == AccountStatus.PAUSED:
            parks.append(account_id)
        return result

    class Rejection(_Response):
        async def __aenter__(self):
            await asyncio.wait_for(upstream_barrier.wait(), 5)
            return self

    def upstream(self, session, *, headers, **kwargs):
        nonlocal rejected
        if headers["Authorization"] == "Bearer empty-key":
            rejected += 1
            if rejected == 2:
                upstream_barrier.set()
            return Rejection(429, {"error": {"code": 1113, "message": "Insufficient balance"}})
        return _Response(200, {"type": "message", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}})

    monkeypatch.setattr(AccountsRepository, "get_by_id", concurrent_snapshot)
    monkeypatch.setattr(AccountsRepository, "update_status_if_current", count_park)
    monkeypatch.setattr(AnthropicProxyService, "_open_upstream_response", upstream)
    # Pin each initial request to the exhausted credential; production retry excludes it.
    original_select = AnthropicProxyService._select_account

    async def select_empty_first(self, model, **kwargs):
        if not kwargs["exclude_account_ids"]:
            async with SessionLocal() as session:
                return await session.get(Account, "empty")
        return await original_select(self, model, **kwargs)

    monkeypatch.setattr(AnthropicProxyService, "_select_account", select_empty_first)
    payload = {"model": "glm-5.2", "max_tokens": 4, "messages": [{"role": "user", "content": "hello"}]}
    responses = await asyncio.gather(*(async_client.post("/v1/messages", json=payload) for _ in range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert rejected == 2
    assert parks == ["empty"]
    async with SessionLocal() as session:
        assert (await session.get(Account, "empty")).status == AccountStatus.PAUSED
        logs = (await session.execute(select(RequestLog))).scalars().all()
        assert sum(log.error_code == "balance_exhausted" for log in logs) == 2
        assert sum(log.status == "success" for log in logs) == 2


@pytest.mark.asyncio
async def test_anthropic_compat_glm_1302_keeps_cooldown(async_client, monkeypatch):
    await _insert_glm("rate-limited", "fixture-key")
    monkeypatch.setattr(
        AnthropicProxyService,
        "_open_upstream_response",
        lambda *args, **kwargs: _Response(429, {"error": {"code": "1302", "message": "Rate limit exceeded"}}),
    )
    response = await async_client.post(
        "/v1/messages", json={"model": "glm-5.2", "max_tokens": 4, "messages": [{"role": "user", "content": "hello"}]}
    )
    assert response.status_code == 429
    async with SessionLocal() as session:
        account = await session.get(Account, "rate-limited")
        assert account.status == AccountStatus.RATE_LIMITED
        assert account.reset_at is not None
        assert not (account.deactivation_reason or "").startswith("balance_exhausted:")


def test_balance_pools_text_columns_and_fallback_shape(monkeypatch, capsys):
    router = runpy.run_path(str(Path(__file__).resolve().parents[2] / "clients/route"))
    document = router["pools_from_accounts"](
        {
            "accounts": [
                {"id": "empty", "provider": "glm", "status": "paused", "deactivationReason": "balance_exhausted: 1113"},
                {"id": "active", "provider": "kimi", "status": "active"},
            ]
        }
    )
    by_id = {pool["id"]: pool for pool in document["pools"]}
    assert by_id["glm"]["unavailableReason"] == "no balance"
    assert "unavailableReason" not in by_id["kimi"]
    globals_ = router["command_pools"].__globals__
    monkeypatch.setitem(globals_, "fetch_pools", lambda **kwargs: (document, None))
    monkeypatch.setitem(globals_, "record_pool_history", lambda *args: None)
    assert router["command_pools"](router["parse_args"](["pools"])) == 0
    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if line.startswith("pool "))
    row = next(line for line in lines if line.startswith("glm "))
    assert row[21:31].strip() == "no balance"
    for label, value in (("headroom", "0.0%"), ("aggregate", "0.0%"), ("usable", "0/1")):
        right = header.index(label) + len(label)
        assert row[right - len(value) : right] == value
