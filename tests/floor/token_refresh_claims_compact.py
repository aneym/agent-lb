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
async def test_compact_preflight_claim_timeout_fails_over_and_releases_lease(async_client, monkeypatch):
    """Route-level regression for the compact freshness-check preflight.

    A transient refresh-claim timeout raised by the compact-responses
    ``_ensure_fresh_with_budget`` preflight (as if a foreign replica holds the
    account's refresh claim) must release the selected account's
    ``response_create`` lease, exclude the account, and fail over to another
    account. Before the fix the compact preflight only translated
    ``aiohttp.ClientError``/``asyncio.TimeoutError``, so the non-permanent
    ``RefreshError`` escaped unhandled and the healthy request errored out with
    an unhandled server error instead of failing over.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_claim_a", "compact-claim-a@example.com"),
        ("acc_compact_claim_b", "compact-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # Whichever account the compact loop freshens first fails with a transient
    # refresh-claim timeout; the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
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

    served_account_ids: list[str] = []
    released_before_compact: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        if not served_account_ids:
            released_before_compact.extend(released_lease_account_ids)
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["output"] == []

    failed_internal_id = first_seen["internal_id"]
    assert failed_internal_id is not None

    async with SessionLocal() as session:
        accounts = {account.id: account for account in (await session.execute(select(Account))).scalars().all()}
        assert {account.status for account in accounts.values()} == {AccountStatus.ACTIVE}
        failed_chatgpt_id = accounts[failed_internal_id].chatgpt_account_id

    # The failed account never served the compact request; a different account did.
    assert served_account_ids
    assert failed_chatgpt_id not in served_account_ids
    # Its response_create lease was released before the failover compact call.
    assert failed_internal_id in released_before_compact


@pytest.mark.asyncio
async def test_compact_preflight_claim_timeout_does_not_penalize_account_health(async_client, monkeypatch):
    """The compact freshness-check transient-claim failover MUST NOT record an
    account-health penalty for peer-claim contention.

    The account's credentials are healthy — only its refresh claim is held by
    another replica — so, like the streaming and WebSocket paths, the compact
    account-attempt loop must merely release + exclude the account and never call
    ``record_error``. Before the fix the branch routed the transient claim
    timeout through ``_handle_stream_error(..., "upstream_unavailable")``, which
    treats ``upstream_unavailable`` as a transient account error and calls
    ``record_error``, pushing an otherwise-healthy account into backoff for
    normal cross-replica claim contention.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_nopenalty_a", "compact-nopenalty-a@example.com"),
        ("acc_compact_nopenalty_b", "compact-nopenalty-b@example.com"),
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
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    recorded_error_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        recorded_error_account_ids.append(account.id)
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
    assert response.json()["output"] == []

    failed_internal_id = first_seen["internal_id"]
    assert failed_internal_id is not None
    # A different, healthy account served the request.
    assert served_account_ids
    # The account that hit peer-claim contention was NOT penalized, and in fact
    # no account was penalized at all for this healthy fallback.
    assert failed_internal_id not in recorded_error_account_ids
    assert recorded_error_account_ids == []

    async with SessionLocal() as session:
        accounts = {account.id: account for account in (await session.execute(select(Account))).scalars().all()}
        assert {account.status for account in accounts.values()} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_compact_post_401_forced_refresh_claim_timeout_fails_over(async_client, monkeypatch):
    """Route-level regression for the compact post-401 forced-refresh path.

    A compact request whose first-selected account returns an upstream 401 must,
    when the subsequent forced (``force=True``) refresh raises a transient
    refresh-claim timeout (claim held by another replica), fail over to a healthy
    account instead of re-raising the misleading original 401. Before the fix the
    compact post-401 branch handled only permanent RefreshErrors and re-raised the
    401 for a transient claim timeout, so the client saw ``invalid_api_key`` for
    what is really transient cross-replica contention.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_p401_a", "compact-p401-a@example.com"),
        ("acc_compact_p401_b", "compact-p401-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds; the post-401 forced
    # refresh (force=True) raises the transient claim timeout for the first
    # account only, the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if not force:
            return account
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    served_account_ids: list[str] = []
    raised_401_for: set[str] = set()

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        # Each account returns a single upstream 401 the first time it is called,
        # forcing the post-401 refresh; after a successful forced refresh it
        # would serve, but the failed account's forced refresh never succeeds.
        if account_id not in raised_401_for:
            raised_401_for.add(account_id)
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["output"] == []
    # A different account served the compact request after the transient failover.
    assert served_account_ids

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_compact_post_401_forced_refresh_claim_timeout_does_not_penalize_account_health(
    async_client, monkeypatch
):
    """The compact post-401 forced-refresh transient-claim failover MUST NOT
    record an account-health penalty for peer-claim contention.

    When a compact account returns an upstream 401 and the subsequent forced
    (``force=True``) refresh raises a transient ``refresh_claim_timeout``
    ``RefreshError`` (claim held by another replica), the account's credentials
    are healthy — only its refresh claim is temporarily owned elsewhere. Before
    the fix this movable branch routed the transient claim timeout through
    ``_handle_stream_error(..., "upstream_unavailable")``, which treats
    ``upstream_unavailable`` as a transient account error and calls
    ``record_error``, pushing an otherwise-healthy account into backoff for
    normal cross-replica contention. Like the preflight and streaming/WebSocket
    paths, this branch must only release + exclude the account and never call
    ``record_error``.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_p401_nopenalty_a", "compact-p401-nopenalty-a@example.com"),
        ("acc_compact_p401_nopenalty_b", "compact-p401-nopenalty-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds; the post-401 forced
    # refresh (force=True) raises the transient claim timeout for the first
    # account only, the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if not force:
            return account
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    recorded_error_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        recorded_error_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []
    raised_401_for: set[str] = set()

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        if account_id not in raised_401_for:
            raised_401_for.add(account_id)
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["output"] == []

    failed_internal_id = first_seen["internal_id"]
    assert failed_internal_id is not None
    # A different, healthy account served the request after failover.
    assert served_account_ids
    # The account that hit peer-claim contention on the forced refresh was NOT
    # penalized, and no account was penalized at all for this healthy fallback.
    assert failed_internal_id not in recorded_error_account_ids
    assert recorded_error_account_ids == []

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_compact_post_401_forced_refresh_claim_timeout_exhaustion_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression: when EVERY compact candidate account returns a 401
    and its post-401 forced refresh raises the transient claim timeout, exhaustion
    must surface a retryable ``upstream_unavailable`` (502) error, not the
    misleading original 401.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_compact_p401x_a", "compact-p401x-a@example.com"),
        ("acc_compact_p401x_b", "compact-p401x-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

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

    # Transient contention surfaces as retryable upstream_unavailable (502), NOT the 401.
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "upstream_unavailable"
    assert body["error"]["code"] != "invalid_api_key"

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}
