from __future__ import annotations

import pytest

from app.core.auth.refresh import RefreshError
from tests.floor.token_refresh_claims_support import (
    _make_auth_json,
)


@pytest.mark.asyncio
async def test_proxy_preflight_genuine_transport_error_penalizes_account(async_client, monkeypatch):
    """Streaming pre-open freshness check: a GENUINE OAuth ``transport_error``
    must RETAIN its account-health penalty (``record_error`` via
    ``_handle_stream_error``) and fail over — NOT the unpenalized claim-contention
    failover reserved for ``refresh_claim_timeout``. The account/route is at fault
    (the refresh request itself failed), so a persistently broken account must be
    pushed into transient backoff instead of being kept healthy and reselected.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_preflight_transport_a", "preflight-transport-a@example.com"),
        ("acc_preflight_transport_b", "preflight-transport-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

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

    penalized_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        penalized_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    streamed_account_ids: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_preflight_transport",'
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
    # It failed over to a healthy account, which served the stream.
    assert failed_account_id not in streamed_account_ids
    assert streamed_account_ids
    # The core regression: the genuine-transport account WAS penalized (unlike a
    # claim-contention timeout, which is not).
    assert failed_account_id in penalized_account_ids


@pytest.mark.asyncio
async def test_proxy_post_401_forced_refresh_genuine_transport_error_penalizes_account(async_client, monkeypatch):
    """Streaming post-401 forced refresh: a GENUINE OAuth ``transport_error`` on
    the forced (``force=True``) refresh must RETAIN its account-health penalty and
    surface a retryable ``upstream_unavailable`` on exhaustion — NOT the
    unpenalized claim-contention failover.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_post401_transport_a", "post401-transport-a@example.com"),
        ("acc_post401_transport_b", "post401-transport-b@example.com"),
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
    assert error_codes == {"upstream_unavailable"}
    # Every candidate account was attempted (each hit the 401 -> forced refresh).
    assert len(set(streamed_account_ids)) == 2
    # The core regression: each genuine-transport account WAS penalized (unlike a
    # claim-contention timeout, which is not).
    assert len(set(penalized_account_ids)) == 2


@pytest.mark.asyncio
async def test_proxy_preflight_genuine_transport_error_releases_lease_before_failover(async_client, monkeypatch):
    """Route-level regression for the pre-401 proactive-refresh GENUINE
    transport-error failover.

    A movable streaming request whose FIRST account hits a genuine OAuth
    ``transport_error`` refresh failure (``code == "transport_error"`` -- NOT
    claim contention) on the proactive freshness check must release the failed
    account's already-acquired stream lease BEFORE failing over to a healthy
    account. Before the fix the generic transport-failure branch recorded the
    health penalty and excluded the account but fell through to ``continue``
    WITHOUT releasing ``current_account_lease``, so the dead account's
    stream-concurrency slot stayed occupied for the entire duration of the
    replacement stream (P2: leaked slot on transport failover).
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_preflight_transport_lease_a", "preflight-transport-lease-a@example.com"),
        ("acc_preflight_transport_lease_b", "preflight-transport-lease-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

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
            'data: {"type":"response.completed","response":{"id":"resp_preflight_transport_lease",'
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
    # Failed over to a healthy account, which served the stream.
    assert failed_account_id not in streamed_account_ids
    assert streamed_account_ids
    assert streamed_account_ids[-1] != failed_account_id
    # The core regression: the genuine-transport account's stream lease was
    # released BEFORE the replacement stream began -- the failover did not hold
    # the dead account's concurrency slot for the duration of the replacement.
    assert failed_account_id in released_before_stream
