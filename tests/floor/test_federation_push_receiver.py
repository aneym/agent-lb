from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core.utils.time import utcnow
from app.db.models import AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy.account_cache import get_account_selection_cache
from tests.floor.federation_push_helpers import _HOUR_MS as _HOUR_MS
from tests.floor.federation_push_helpers import _LOCAL_INSTANCE_ID as _LOCAL_INSTANCE_ID
from tests.floor.federation_push_helpers import _MIRROR_TOKEN as _MIRROR_TOKEN
from tests.floor.federation_push_helpers import _PULL_PEER_INSTANCE_ID as _PULL_PEER_INSTANCE_ID
from tests.floor.federation_push_helpers import _SENDER_INSTANCE_ID as _SENDER_INSTANCE_ID
from tests.floor.federation_push_helpers import _STRANGER_IP as _STRANGER_IP
from tests.floor.federation_push_helpers import _account as _account
from tests.floor.federation_push_helpers import _decrypt as _decrypt
from tests.floor.federation_push_helpers import _get_account as _get_account
from tests.floor.federation_push_helpers import _nate_source as _nate_source
from tests.floor.federation_push_helpers import _push as _push
from tests.floor.federation_push_helpers import _push_body as _push_body
from tests.floor.federation_push_helpers import _seed_account as _seed_account
from tests.floor.federation_push_helpers import _snapshot_accounts as _snapshot_accounts
from tests.floor.federation_push_helpers import _write_sources as _write_sources
from tests.floor.federation_push_helpers import sources_path as sources_path
from tests.floor.federation_push_helpers import whois_calls as whois_calls

pytestmark = pytest.mark.integration


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

    third = await _push(app_instance, _push_body(_account("x"), _account("y", token="pushed-access-y-2")))
    assert third.status_code == 200, third.text
    assert sorted(third.json()["accepted"]) == ["x", "y"]
    y_row = await _get_account("y")
    assert y_row is not None and y_row.status == AccountStatus.ACTIVE
    assert _decrypt(y_row.access_token_encrypted) == "pushed-access-y-2"

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


@pytest.mark.asyncio
async def test_usage_is_filtered_to_accepted_accounts(
    async_client, app_instance, sources_path: Path, whois_calls
) -> None:
    await _seed_account("o", owner_instance=_LOCAL_INSTANCE_ID)
    _write_sources(sources_path, [_nate_source()])

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
