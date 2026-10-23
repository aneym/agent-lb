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
async def test_proxy_pinned_stream_claim_timeout_stays_on_owner_and_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression for the hard-pinned streaming preflight sub-case.

    A stream turn pinned by ``previous_response_id`` sets ``preferred_account_id``
    (and ``require_preferred_account``), so the movable transient-claim failover
    branch is correctly skipped -- a pinned request must not cross accounts. But
    before the fix the pinned path then fell through to the unconditional
    ``continue``: it reselected the same owner account on every attempt without
    releasing the already-acquired stream lease or recording a retryable error,
    leaking a stream-concurrency slot each iteration and finally surfacing a
    misleading ``no_accounts`` result once attempts were exhausted.

    The fix keeps the request on its owner account (no crossing) but releases the
    lease and surfaces a retryable ``upstream_unavailable`` promptly.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_pinned_claim_a", "pinned-claim-a@example.com"),
        ("acc_pinned_claim_b", "pinned-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = sorted((await session.execute(select(Account.id))).scalars().all())
    assert len(account_ids) == 2
    owner_account_id = account_ids[0]
    alternate_account_id = account_ids[1]

    # Pin the turn to the owner account: ``previous_response_id`` resolves to the
    # owner, setting preferred_account_id and require_preferred_account.
    async def fake_owner(self, *, previous_response_id, api_key, session_id=None, surface):
        del self, previous_response_id, api_key, session_id, surface
        return owner_account_id

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_owner)

    # The pinned owner account's proactive freshness check raises a transient
    # refresh-claim timeout (claim held by a foreign replica). No other account
    # should ever be freshened, because the pinned request must not cross.
    freshened_account_ids: list[str] = []

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        freshened_account_ids.append(account.id)
        raise RefreshError(
            "refresh_claim_timeout",
            "refresh claim held by another replica",
            False,
            transport_error=True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None:
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

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
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "previous_response_id": "resp_pinned_owner",
        },
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    failed_events = [event for event in events if event.get("type") == "response.failed"]
    assert failed_events, f"expected a response.failed event, got {events}"
    error_codes = {event["response"]["error"]["code"] for event in failed_events}
    # Pinned transient contention surfaces as retryable upstream_unavailable, NOT
    # a misleading no_accounts / previous_response_owner_unavailable exhaustion.
    assert error_codes == {"upstream_unavailable"}
    assert "no_accounts" not in error_codes
    assert "previous_response_owner_unavailable" not in error_codes
    # No stream ever opened, and the request never crossed to the alternate
    # account (the account-ownership invariant is preserved).
    assert streamed_account_ids == []
    assert alternate_account_id not in freshened_account_ids
    assert freshened_account_ids == [owner_account_id]
    # The pinned owner's already-acquired stream lease was released (not leaked).
    assert owner_account_id in released_lease_account_ids

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_post_401_forced_refresh_claim_timeout_exhaustion_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression for the post-401 forced-refresh sibling branch.

    When EVERY candidate account opens far enough to receive an upstream 401 and
    its subsequent forced (``force=True``) refresh raises a transient refresh-claim
    timeout (claim held by another replica), exhaustion must surface a retryable
    ``upstream_unavailable`` error, not a misleading generic ``no_accounts``.

    Before the fix the post-401 forced-refresh transient-claim branch released
    the lease and excluded the account but left ``last_retryable_stream_error``
    unset (unlike its pre-stream-open sibling), so after attempts were exhausted
    the loop fell through to the no-accounts path.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_post401_claim_a", "post401-claim-a@example.com"),
        ("acc_post401_claim_b", "post401-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds so the stream opens;
    # the post-401 forced refresh (force=True) raises the transient claim timeout
    # for EVERY account, as if a foreign replica holds each account's claim.
    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if force:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    streamed_account_ids: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        raise proxy_module.ProxyResponseError(
            401,
            {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
        )
        yield ""  # pragma: no cover - makes this an async generator

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
    # Every candidate account was attempted (each hit the 401 -> transient forced refresh).
    assert len(set(streamed_account_ids)) == 2

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_pinned_stream_post_401_claim_timeout_stays_on_owner_and_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression for the hard-pinned POST-401 forced-refresh sub-case.

    A stream turn pinned by ``previous_response_id`` sets ``preferred_account_id``
    (and ``require_preferred_account``). The proactive freshness check succeeds so
    the stream opens on the owner, but the upstream returns a 401 and the
    subsequent forced (``force=True``) refresh raises a transient refresh-claim
    timeout (claim held by another replica). Because ``preferred_account_id`` is
    set, the movable post-401 failover branch is correctly skipped -- a pinned
    request must not cross accounts.

    Before the fix the pinned post-401 sub-case fell through to the unconditional
    ``continue``: it reselected the same owner account on every attempt without
    releasing the already-acquired stream lease or recording a retryable error,
    leaking a stream-concurrency slot each iteration and finally surfacing a
    misleading ``no_accounts`` result once attempts were exhausted.

    The fix keeps the request on its owner account (no crossing) but releases the
    lease and surfaces a retryable ``upstream_unavailable`` promptly.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_pinned_post401_a", "pinned-post401-a@example.com"),
        ("acc_pinned_post401_b", "pinned-post401-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        rows = (await session.execute(select(Account.id, Account.chatgpt_account_id))).all()
    upstream_by_internal = {internal: upstream for internal, upstream in rows}
    account_ids = sorted(upstream_by_internal)
    assert len(account_ids) == 2
    owner_account_id = account_ids[0]
    alternate_account_id = account_ids[1]
    owner_upstream_id = upstream_by_internal[owner_account_id]
    alternate_upstream_id = upstream_by_internal[alternate_account_id]

    # Pin the turn to the owner account: ``previous_response_id`` resolves to the
    # owner, setting preferred_account_id and require_preferred_account.
    async def fake_owner(self, *, previous_response_id, api_key, session_id=None, surface):
        del self, previous_response_id, api_key, session_id, surface
        return owner_account_id

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_owner)

    # The proactive freshness check (force=False) succeeds so the stream opens;
    # the post-401 forced refresh (force=True) raises the transient claim timeout,
    # as if a foreign replica holds the pinned owner's claim. No other account may
    # ever be freshened because the pinned request must not cross.
    freshened_force_flags: list[tuple[str, bool]] = []

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        freshened_force_flags.append((account.id, force))
        if force:
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

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        raise proxy_module.ProxyResponseError(
            401,
            {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
        )
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "previous_response_id": "resp_pinned_owner",
        },
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    failed_events = [event for event in events if event.get("type") == "response.failed"]
    assert failed_events, f"expected a response.failed event, got {events}"
    error_codes = {event["response"]["error"]["code"] for event in failed_events}
    # Pinned transient contention surfaces as retryable upstream_unavailable, NOT
    # a misleading no_accounts / previous_response_owner_unavailable exhaustion.
    assert error_codes == {"upstream_unavailable"}
    assert "no_accounts" not in error_codes
    assert "previous_response_owner_unavailable" not in error_codes
    # The stream only ever opened on the owner (it hit a 401), and the request
    # never crossed to the alternate account for a stream or a forced refresh.
    assert streamed_account_ids == [owner_upstream_id]
    assert alternate_upstream_id not in streamed_account_ids
    assert all(account_id == owner_account_id for account_id, _ in freshened_force_flags)
    assert alternate_account_id not in {account_id for account_id, _ in freshened_force_flags}
    # The transient claim timeout came from the forced (post-401) refresh.
    assert (owner_account_id, True) in freshened_force_flags
    # The pinned owner's already-acquired stream lease was released (not leaked).
    assert owner_account_id in released_lease_account_ids

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}
