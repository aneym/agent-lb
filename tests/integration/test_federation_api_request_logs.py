from __future__ import annotations
import pytest
from sqlalchemy import select
from app.db.models import Account, AccountStatus, AccountTransferDirection, FederationUsageDaily, RequestLog
from app.db.session import SessionLocal
from tests.integration.federation_api_support import (
    _auth_headers,
    _enable_federation,
    _request_log_row,
    _seed_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_request_logs_ingest_is_idempotent_and_requires_mirror_auth(async_client, monkeypatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("known-acct", owner_instance=None)
    payload = {
        "instance_id": "ax42",
        "rows": [
            _request_log_row(11, "req-known", "known-acct"),
            _request_log_row(12, "req-unknown", "missing-acct"),
        ],
    }

    denied = await async_client.post("/api/federation/request-logs", json=payload)
    assert denied.status_code == 403
    wrong = await async_client.post(
        "/api/federation/request-logs", json=payload, headers={"Authorization": "Bearer wrong-token"}
    )
    assert wrong.status_code == 403

    accepted = await async_client.post("/api/federation/request-logs", json=payload, headers=_auth_headers())
    assert accepted.status_code == 200
    assert accepted.json() == {"accepted": 2, "skipped": 0, "max_source_row_id": 12}

    again = await async_client.post("/api/federation/request-logs", json=payload, headers=_auth_headers())
    assert again.status_code == 200
    assert again.json() == {"accepted": 0, "skipped": 2, "max_source_row_id": 12}

    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).order_by(RequestLog.request_id))).scalars().all()
    assert len(rows) == 2
    by_request = {row.request_id: row for row in rows}
    known = by_request["req-known"]
    unknown = by_request["req-unknown"]
    assert known.source == "edge:ax42"
    assert unknown.source == "edge:ax42"
    assert known.api_key_id is None
    assert unknown.api_key_id is None
    assert known.account_id == "known-acct"
    assert unknown.account_id is None
    assert known.room == "lab"
    assert known.caller_seat == "alex"
    assert known.unified_5h_utilization == 0.25
    assert known.unified_7d_utilization == 0.5
    assert known.input_tokens == 11


@pytest.mark.asyncio
async def test_request_logs_rejects_batches_over_500(async_client, monkeypatch) -> None:
    _enable_federation(monkeypatch)
    rows = [_request_log_row(index, f"bulk-{index}", None) for index in range(1, 502)]
    response = await async_client.post(
        "/api/federation/request-logs",
        json={"instance_id": "ax42", "rows": rows},
        headers=_auth_headers(),
    )
    assert response.status_code in {413, 422}
