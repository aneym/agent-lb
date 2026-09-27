from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.identity import RequestIdentity
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestKind, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _account(account_id: str, *, canceled: bool = False) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.invalid",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        subscription_status="canceled" if canceled else "active",
    )


@pytest.mark.asyncio
async def test_request_list_exposes_four_caller_fields_and_old_null_rows(async_client, db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        await repo.add_log(
            account_id=None,
            request_id="req_named",
            model="model-a",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            identity=RequestIdentity("owner-a", "owner-machine", "box-1", "tailnet"),
        )
        session.add(RequestLog(request_id="req_old", model="model-a", status="success"))
        await session.commit()

    response = await async_client.get("/api/request-logs?limit=10")
    assert response.status_code == 200
    rows = {row["requestId"]: row for row in response.json()["requests"]}
    assert {
        key: rows["req_named"][key]
        for key in ("callerUser", "callerUserSource", "callerMachine", "callerMachineSource")
    } == {
        "callerUser": "owner-a",
        "callerUserSource": "owner-machine",
        "callerMachine": "box-1",
        "callerMachineSource": "tailnet",
    }
    assert all(
        rows["req_old"][key] is None
        for key in ("callerUser", "callerUserSource", "callerMachine", "callerMachineSource")
    )


@pytest.mark.asyncio
async def test_caller_summary_bounds_window_and_matches_usage_exclusions(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        await accounts.upsert(_account("acc_active"))
        await accounts.upsert(_account("acc_canceled", canceled=True))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_active",
                    request_id="req_active",
                    model="model-a",
                    status="success",
                    requested_at=now - timedelta(minutes=10),
                    deleted_at=now,
                    caller_user="owner-a",
                    caller_machine="box-1",
                    input_tokens=11,
                    output_tokens=7,
                    cost_usd=0.125,
                ),
                RequestLog(
                    account_id=None,
                    request_id="req_old",
                    model="model-a",
                    status="success",
                    requested_at=now - timedelta(minutes=20),
                    input_tokens=3,
                    reasoning_tokens=2,
                    cost_usd=0.25,
                ),
                RequestLog(
                    account_id="acc_active",
                    request_id="req_warmup",
                    model="model-a",
                    status="success",
                    request_kind=RequestKind.WARMUP.value,
                    requested_at=now,
                    caller_user="owner-a",
                    caller_machine="box-1",
                    input_tokens=90,
                    cost_usd=1,
                ),
                RequestLog(
                    account_id="acc_active",
                    request_id="req_limit_warmup",
                    model="model-a",
                    status="success",
                    request_kind="limit_warmup",
                    requested_at=now,
                    input_tokens=90,
                    cost_usd=1,
                ),
                RequestLog(
                    account_id="acc_canceled",
                    request_id="req_canceled",
                    model="model-a",
                    status="success",
                    requested_at=now,
                    input_tokens=90,
                    cost_usd=1,
                ),
                RequestLog(
                    account_id="acc_active",
                    request_id="req_old_window",
                    model="model-a",
                    status="success",
                    requested_at=now - timedelta(hours=2),
                    input_tokens=90,
                    cost_usd=1,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/api/usage/callers?hours=1")
    assert response.status_code == 200
    assert response.json() == {
        "windowHours": 1,
        "callers": [
            {
                "callerUser": "owner-a",
                "callerMachine": "box-1",
                "requests": 1,
                "inputTokens": 11,
                "outputTokens": 7,
                "costUsd": 0.125,
            },
            {
                "callerUser": "unattributed",
                "callerMachine": "unattributed",
                "requests": 1,
                "inputTokens": 3,
                "outputTokens": 2,
                "costUsd": 0.25,
            },
        ],
    }
    assert (await async_client.get("/api/usage/callers?hours=0")).status_code == 422
    assert (await async_client.get("/api/usage/callers?hours=169")).status_code == 422
