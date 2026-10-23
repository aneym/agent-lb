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
async def test_proxy_post_401_permanent_refresh_releases_lease_before_failover(async_client, monkeypatch):
    """Route-level regression for the post-401 forced-refresh PERMANENT branch.

    The proactive freshness check succeeds so the stream opens; the upstream
    returns a 401 and the subsequent forced (``force=True``) refresh fails
    PERMANENTLY. The account is marked permanently failed and the request fails
    over to another account. As with the proactive permanent branch, the failed
    account's already-acquired stream lease must be released BEFORE the failover
    ``continue`` so its stream-concurrency slot is not held for the entire
    duration of the replacement stream.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_post401_perm_a", "post401-perm-a@example.com"),
        ("acc_post401_perm_b", "post401-perm-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds so the stream opens;
    # the post-401 forced refresh (force=True) fails PERMANENTLY for the first
    # account that reaches it. The other account never receives a 401.
    first_forced: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if force:
            if first_forced["account_id"] is None:
                first_forced["account_id"] = account.id
            if account.id == first_forced["account_id"]:
                raise RefreshError(
                    "refresh_token_reused",
                    "refresh token reused",
                    True,
                )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    # Track only the tracked stream-concurrency lease (kind == "stream"). The
    # failed account opens a real stream (to receive the 401), so ``_stream_once``
    # acquires and releases a separate ``response_create`` lease for it; without
    # this filter that unrelated release would mask whether the stream-concurrency
    # slot itself was freed at the permanent-failure failover.
    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None and lease.kind == "stream":
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []
    released_before_failover_stream: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        # First account opens then receives a 401 (triggering the forced
        # refresh that fails permanently). The failover account streams cleanly.
        if not streamed_account_ids:
            streamed_account_ids.append(account_id)
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        released_before_failover_stream.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_post401_perm_failover",'
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

    failed_account_id = first_forced["account_id"]
    assert failed_account_id is not None
    # Two distinct accounts streamed: the one that opened, took the 401, and
    # permanently failed its forced refresh, then the failover account.
    assert len(set(streamed_account_ids)) == 2
    assert streamed_account_ids[0] != streamed_account_ids[-1]
    # The permanently-failed account's stream-concurrency lease was released
    # BEFORE the failover account started streaming (no leaked slot held for the
    # replacement stream's duration).
    assert failed_account_id in released_before_failover_stream

    async with SessionLocal() as session:
        status_by_id = dict((await session.execute(select(Account.id, Account.status))).all())
    assert status_by_id[failed_account_id] == AccountStatus.REAUTH_REQUIRED
    other_id = next(account_id for account_id in status_by_id if account_id != failed_account_id)
    assert status_by_id[other_id] == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_proxy_preflight_claim_timeout_exhaustion_reports_upstream_unavailable(async_client, monkeypatch):
    """Route-level regression: when EVERY candidate account hits a transient
    refresh-claim timeout on the proactive freshness check before the stream
    opens, exhaustion must surface a retryable ``upstream_unavailable`` error,
    not a misleading generic ``no_accounts`` response.

    Before the fix the transient-claim failover branch released the lease and
    excluded the account but left ``last_retryable_stream_error`` unset, so after
    attempts were exhausted the loop fell through to the no-accounts path and the
    client saw ``no_accounts`` for what is really transient contention.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_exhaust_claim_a", "exhaust-claim-a@example.com"),
        ("acc_exhaust_claim_b", "exhaust-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # EVERY account's proactive freshness check raises a transient refresh-claim
    # timeout (as if a foreign replica holds each account's claim).
    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        raise RefreshError(
            "refresh_claim_timeout",
            "refresh claim held by another replica",
            False,
            transport_error=True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    streamed_account_ids: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_should_not_open",'
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
    failed_events = [event for event in events if event.get("type") == "response.failed"]
    assert failed_events, f"expected a response.failed event, got {events}"
    error_codes = {event["response"]["error"]["code"] for event in failed_events}
    # Transient contention surfaces as retryable upstream_unavailable, NOT no_accounts.
    assert error_codes == {"upstream_unavailable"}
    assert "no_accounts" not in error_codes
    # No stream ever opened (every candidate failed its freshness check).
    assert streamed_account_ids == []

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}
