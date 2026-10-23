from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.db.models import Account, AccountStatus, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.refresh_claims import RefreshClaimCoordinator
from tests.floor.token_refresh_claims_support import (
    _insert_claim,
    _make_auth_json,
)


@pytest.mark.asyncio
async def test_proxy_401_with_foreign_claim_fails_over_without_reauth_write(async_client, monkeypatch):
    """Route-level regression (failed pre-claims by marking REAUTH_REQUIRED and
    deleting sticky sessions): an upstream 401 forces a token refresh while a
    foreign replica holds the account's refresh claim. The request must fail
    over to another account within the bounded wait, with zero upstream token
    exchanges and no status/sticky teardown."""
    import json

    import app.modules.proxy.service as proxy_module
    from app.modules.accounts.refresh_claims import set_refresh_claim_coordinator

    monkeypatch.setattr(auth_manager_module, "_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(auth_manager_module, "_TOKEN_REFRESH_CLAIM_POLL_SECONDS", 0.05)
    set_refresh_claim_coordinator(RefreshClaimCoordinator(claimant_id="this-replica"))

    for raw_account_id, email in (
        ("acc_claim_route_a", "claim-route-a@example.com"),
        ("acc_claim_route_b", "claim-route-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()
    for account_id in account_ids:
        await _insert_claim(account_id, claimed_by="other-replica", expires_in_seconds=60)

    refresh_exchange_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_exchange_calls
        refresh_exchange_calls += 1
        # Pre-claims this is what the race loser received upstream, and it
        # marked the account REAUTH_REQUIRED.
        raise RefreshError("refresh_token_reused", "refresh token reused", True, phase=ExchangePhase.ANSWERED)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    invalidated_account_id: str | None = None
    captured_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_claim_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)
    assert captured_account_ids[0] == invalidated_account_id
    assert captured_account_ids[-1] != invalidated_account_id

    # The refresh claim was honored: no upstream token exchange ran at all.
    assert refresh_exchange_calls == 0

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}
        sticky_keys = set((await session.execute(select(StickySession.key))).scalars().all())
        assert {f"sticky-{account_id}" for account_id in account_ids} <= sticky_keys


@pytest.mark.asyncio
async def test_proxy_preflight_claim_timeout_fails_over_and_releases_lease(async_client, monkeypatch):
    """Route-level regression for the pre-401 proactive-refresh path.

    A transient refresh-claim timeout on the FIRST stream attempt (the
    proactive freshness check, before any upstream 401) must exclude the
    account, release its already-acquired stream lease, and fail over to
    another account. Before the fix the streaming retry loop only handled a
    transient claim failure in the post-401 forced-refresh path, so a
    first-attempt claim timeout propagated out of the generator (P2 #1) and,
    even where excluded, leaked the skipped account's stream lease (P2 #2).
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_preflight_claim_a", "preflight-claim-a@example.com"),
        ("acc_preflight_claim_b", "preflight-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()

    # Whichever account the retry loop freshens first fails with a transient
    # refresh-claim timeout (as if a foreign replica holds the claim on the
    # proactive, pre-401 freshness check); the other account freshens cleanly.
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

    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None:
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []
    released_before_stream: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        if not streamed_account_ids:
            released_before_stream.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_preflight_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)

    failed_account_id = first_seen["account_id"]
    assert failed_account_id is not None
    # The pre-401 claim timeout excluded the failed account and failed over: it
    # never reached the upstream stream, and a different account served it.
    assert failed_account_id not in streamed_account_ids
    assert streamed_account_ids
    assert streamed_account_ids[-1] != failed_account_id
    # The skipped account's stream lease was released BEFORE failover streaming
    # (no leaked lease).
    assert failed_account_id in released_before_stream

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_preflight_permanent_refresh_releases_lease_before_failover(async_client, monkeypatch):
    """Route-level regression for the pre-401 proactive-refresh PERMANENT branch.

    A permanent ``RefreshError`` on the FIRST stream attempt (the proactive
    freshness check, before any upstream 401) marks the account permanently
    failed and fails over to another account. The failed account is removed from
    selection, but its already-acquired stream lease must be released BEFORE the
    failover ``continue`` -- otherwise the dead account's stream-concurrency slot
    stays occupied for the entire duration of the replacement stream. Before the
    fix the ``is_permanent`` branch fell through to ``continue`` without
    releasing ``current_account_lease`` (P2: leaked slot on permanent failover).
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_preflight_perm_a", "preflight-perm-a@example.com"),
        ("acc_preflight_perm_b", "preflight-perm-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-perm-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()

    # Whichever account the retry loop freshens first fails PERMANENTLY on the
    # proactive, pre-401 freshness check; the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["account_id"] is None:
            first_seen["account_id"] = account.id
        if account.id == first_seen["account_id"]:
            raise RefreshError(
                "refresh_token_reused",
                "refresh token reused",
                True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    # Track only the tracked stream-concurrency lease (kind == "stream"); the
    # per-attempt ``response_create`` lease acquired inside ``_stream_once`` is a
    # DIFFERENT lease and would otherwise mask whether the stream-concurrency
    # slot was actually freed at failover.
    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None and lease.kind == "stream":
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []
    released_before_stream: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        if not streamed_account_ids:
            released_before_stream.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_preflight_perm_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)

    failed_account_id = first_seen["account_id"]
    assert failed_account_id is not None
    # The permanent failure removed the failed account from selection and failed
    # over: it never reached the upstream stream, and a different account served.
    assert failed_account_id not in streamed_account_ids
    assert streamed_account_ids
    assert streamed_account_ids[-1] != failed_account_id
    # The failed account's stream lease was released BEFORE failover streaming
    # (no leaked concurrency slot held for the replacement stream's duration).
    assert failed_account_id in released_before_stream

    # The permanent failure was recorded; the failover account stays ACTIVE.
    async with SessionLocal() as session:
        status_by_id = dict((await session.execute(select(Account.id, Account.status))).all())
    assert status_by_id[failed_account_id] == AccountStatus.REAUTH_REQUIRED
    other_id = next(account_id for account_id in status_by_id if account_id != failed_account_id)
    assert status_by_id[other_id] == AccountStatus.ACTIVE
