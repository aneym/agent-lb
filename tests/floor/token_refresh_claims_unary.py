from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.auth.refresh import RefreshError
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from tests.floor.token_refresh_claims_support import (
    _make_auth_json,
)


@pytest.mark.asyncio
async def test_proxy_previsible_unary_claim_timeout_fails_over_without_health_penalty(async_client, monkeypatch):
    """Route-level regression for the movable previsible-unary path.

    A movable previsible-unary request (here a thread-goal GET) whose proactive
    freshness check hits a transient refresh-claim timeout must fail over to a
    healthy account WITHOUT recording a health penalty against the claim-held
    account. Before the fix ``_ensure_previsible_unary_fresh_with_failover``
    called ``_handle_stream_error(..., "upstream_unavailable")`` on the skipped
    account, which recorded a ``record_error`` health/backoff penalty for what is
    merely normal cross-replica claim contention (the streaming/compact/WebSocket
    siblings were already fixed; this was the remaining one).
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_previsible_claim_a", "previsible-claim-a@example.com"),
        ("acc_previsible_claim_b", "previsible-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # Whichever account the failover loop freshens first hits a transient
    # refresh-claim timeout (as if a foreign replica holds its claim on the
    # proactive freshness check); the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["account_id"] is None:
            first_seen["account_id"] = account.id
        if account.id == first_seen["account_id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    # Spy on the health-penalty routine: it must never fire for the claim-held
    # account (nor any account, since the served account succeeds).
    penalized_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        penalized_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []

    async def fake_thread_goal(operation, payload, headers, access_token, account_id, **_kwargs):
        del operation, headers, access_token, _kwargs
        served_account_ids.append(account_id)
        return {
            "goal": {
                "threadId": payload["threadId"],
                "objective": "ship the proxy",
                "status": "active",
                "tokenBudget": None,
                "tokensUsed": 0,
                "timeBudgetSeconds": None,
                "timeUsedSeconds": 0,
                "createdAt": 1,
                "updatedAt": 1,
            }
        }

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    thread_id = "019debd9-2372-7f23-92b9-9f34002a6355"
    response = await async_client.get(
        "/backend-api/codex/thread/goal/get",
        params={"threadId": thread_id},
    )
    assert response.status_code == 200
    assert response.json()["goal"]["objective"] == "ship the proxy"

    failed_account_id = first_seen["account_id"]
    assert failed_account_id is not None
    # The claim timeout excluded the failed account and failed over: it never
    # served the request, and a different account did.
    assert failed_account_id not in served_account_ids
    assert served_account_ids
    assert served_account_ids[-1] != failed_account_id
    # The core regression: the claim-held account was NOT penalized.
    assert failed_account_id not in penalized_account_ids
    assert penalized_account_ids == []

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_previsible_unary_claim_timeout_exhaustion_reports_upstream_unavailable(async_client, monkeypatch):
    """Route-level regression: when EVERY candidate account hits a transient
    refresh-claim timeout on the previsible-unary freshness check, exhaustion must
    surface a retryable ``upstream_unavailable`` WITHOUT penalizing the last
    claim-held account. Before the fix the caller's terminal ``_handle_proxy_error``
    recorded a health penalty on the final account for pure claim contention.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_previsible_exhaust_a", "previsible-exhaust-a@example.com"),
        ("acc_previsible_exhaust_b", "previsible-exhaust-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # EVERY account's freshness check raises a transient refresh-claim timeout.
    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        raise RefreshError(
            "refresh_claim_timeout",
            "refresh claim held by another replica",
            False,
            transport_error=True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    penalized_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        penalized_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []

    async def fake_thread_goal(operation, payload, headers, access_token, account_id, **_kwargs):
        del operation, payload, headers, access_token, _kwargs
        served_account_ids.append(account_id)
        return {"goal": None}

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    thread_id = "019debd9-2372-7f23-92b9-9f34002a6355"
    response = await async_client.get(
        "/backend-api/codex/thread/goal/get",
        params={"threadId": thread_id},
    )
    # Transient contention surfaces as a retryable upstream_unavailable, and the
    # upstream goal call never ran (every candidate failed its freshness check).
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert served_account_ids == []
    # The core regression: no account was penalized for pure claim contention.
    assert penalized_account_ids == []

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_previsible_unary_genuine_transport_error_penalizes_account(async_client, monkeypatch):
    """Route-level regression: a GENUINE OAuth transport failure on the movable
    previsible-unary freshness check must RETAIN its account-health penalty.

    Unlike cross-replica refresh-claim contention (``refresh_claim_timeout`` and
    the sibling claim/CAS codes), a ``code == "transport_error"`` ``RefreshError``
    means the OAuth refresh request itself timed out / its upstream connection
    failed — that IS the account/route's fault. The previsible-unary failover
    path must still call ``_handle_stream_error`` (``record_error``) on the
    skipped account so a persistently broken account is pushed into transient
    backoff instead of being reselected on the next request. Commit c4e5f5e6
    over-broadened the no-penalty behaviour from claim contention to EVERY
    transport error, dropping this penalty; this guards the restored behaviour.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_previsible_transport_a", "previsible-transport-a@example.com"),
        ("acc_previsible_transport_b", "previsible-transport-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # Whichever account is freshened first raises a GENUINE OAuth transport
    # RefreshError (``code == "transport_error"``) whose message is a retryable
    # transport marker so the path fails over; the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["account_id"] is None:
            first_seen["account_id"] = account.id
        if account.id == first_seen["account_id"]:
            raise RefreshError(
                "transport_error",
                "Transport error during token refresh: connection reset",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    # Spy on the health-penalty routine: it MUST fire for the genuine-transport
    # account (the core assertion of this regression).
    penalized_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        penalized_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []

    async def fake_thread_goal(operation, payload, headers, access_token, account_id, **_kwargs):
        del operation, headers, access_token, _kwargs
        served_account_ids.append(account_id)
        return {
            "goal": {
                "threadId": payload["threadId"],
                "objective": "ship the proxy",
                "status": "active",
                "tokenBudget": None,
                "tokensUsed": 0,
                "timeBudgetSeconds": None,
                "timeUsedSeconds": 0,
                "createdAt": 1,
                "updatedAt": 1,
            }
        }

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    thread_id = "019debd9-2372-7f23-92b9-9f34002a6355"
    response = await async_client.get(
        "/backend-api/codex/thread/goal/get",
        params={"threadId": thread_id},
    )
    assert response.status_code == 200
    assert response.json()["goal"]["objective"] == "ship the proxy"

    failed_account_id = first_seen["account_id"]
    assert failed_account_id is not None
    # It failed over to a healthy account, which served the request.
    assert failed_account_id not in served_account_ids
    assert served_account_ids
    assert served_account_ids[-1] != failed_account_id
    # The core regression: the genuine-transport account WAS penalized, unlike a
    # claim-contention timeout which is not.
    assert failed_account_id in penalized_account_ids
