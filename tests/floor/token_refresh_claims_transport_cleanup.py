from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.auth.refresh import RefreshError
from app.db.models import Account
from app.db.session import SessionLocal
from tests.floor.token_refresh_claims_support import (
    _make_auth_json,
)


@pytest.mark.asyncio
async def test_proxy_post_401_forced_refresh_genuine_transport_error_releases_lease_before_failover(
    async_client, monkeypatch
):
    """Route-level regression for the post-401 forced-refresh GENUINE
    transport-error failover.

    A movable streaming request whose FIRST account returns a 401 and then hits
    a genuine OAuth ``transport_error`` on the forced (``force=True``) refresh
    must release that account's stream lease BEFORE failing over. This exercises
    the post-401 sibling of the pre-401 generic transport-failure branch, which
    the class sweep hardened to release ``current_account_lease`` before its
    ``continue`` so the dead account's stream-concurrency slot is not held for
    the duration of the replacement stream.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_post401_transport_lease_a", "post401-transport-lease-a@example.com"),
        ("acc_post401_transport_lease_b", "post401-transport-lease-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    forced_fail: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        # Proactive freshness always succeeds; only the post-401 FORCED refresh
        # fails with a genuine transport error, which happens for the first
        # account (the one that received the upstream 401).
        if force:
            if forced_fail["account_id"] is None:
                forced_fail["account_id"] = account.id
            raise RefreshError(
                "transport_error",
                "Transport error during token refresh: connection reset",
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
    released_before_replacement: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        if not streamed_account_ids:
            # First account: return a 401 so the retry loop performs the forced
            # refresh (which fails with a genuine transport error).
            streamed_account_ids.append(account_id)
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
            yield ""  # pragma: no cover - makes this an async generator
        # Replacement stream on the healthy account: capture which leases were
        # already released by the time the failover stream begins.
        released_before_replacement.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_post401_transport_lease",'
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

    assert len(streamed_account_ids) >= 2
    failed_account_id = forced_fail["account_id"]
    assert failed_account_id is not None
    # Failed over to a different, healthy account after the forced-refresh
    # transport error.
    assert streamed_account_ids[-1] != streamed_account_ids[0]
    # The core regression: the first account's stream lease was released BEFORE
    # the replacement stream began.
    assert failed_account_id in released_before_replacement


@pytest.mark.asyncio
async def test_compact_preflight_genuine_transport_error_penalizes_account(async_client, monkeypatch):
    """Compact freshness-check preflight: a GENUINE OAuth ``transport_error`` must
    RETAIN its account-health penalty and fail over — NOT the unpenalized
    claim-contention failover reserved for ``refresh_claim_timeout``.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_transport_a", "compact-transport-a@example.com"),
        ("acc_compact_transport_b", "compact-transport-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
            raise RefreshError(
                "transport_error",
                "Transport error during token refresh: connection reset",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    penalized_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        penalized_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    failed_internal_id = first_seen["internal_id"]
    assert failed_internal_id is not None
    async with SessionLocal() as session:
        accounts = {account.id: account for account in (await session.execute(select(Account))).scalars().all()}
        failed_chatgpt_id = accounts[failed_internal_id].chatgpt_account_id
    # It failed over to a healthy account.
    assert served_account_ids
    assert failed_chatgpt_id not in served_account_ids
    # The core regression: the genuine-transport account WAS penalized.
    assert failed_internal_id in penalized_account_ids


@pytest.mark.asyncio
async def test_compact_post_401_forced_refresh_genuine_transport_error_penalizes_account(async_client, monkeypatch):
    """Compact post-401 forced refresh: a GENUINE OAuth ``transport_error`` on the
    forced (``force=True``) refresh must RETAIN its account-health penalty and
    surface a retryable ``upstream_unavailable`` on exhaustion — NOT the
    unpenalized claim-contention failover.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_compact_post401_transport_a", "compact-post401-transport-a@example.com"),
        ("acc_compact_post401_transport_b", "compact-post401-transport-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if force:
            raise RefreshError(
                "transport_error",
                "Transport error during token refresh: connection reset",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    penalized_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        penalized_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        served_account_ids.append(account_id)
        raise proxy_module.ProxyResponseError(
            401,
            {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    # Every candidate's forced refresh genuine-transport-failed → retryable 502.
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    # Every candidate account was attempted (each hit the 401 -> forced refresh).
    assert len(set(served_account_ids)) == 2
    # The core regression: each genuine-transport account WAS penalized (unlike a
    # claim-contention timeout, which is not).
    assert len(set(penalized_account_ids)) == 2
