from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountResumeSchedule, AccountStatus
from app.db.session import SessionLocal
from app.modules.account_schedule.scheduler import resume_due_accounts_once

pytestmark = pytest.mark.integration


async def _create_account(account_id: str) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider="openai",
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_resume_schedule_reactivates_only_due_paused_accounts(async_client):
    await _create_account("due")
    await _create_account("future")
    await _create_account("manual")
    now = utcnow()
    past = (now - timedelta(minutes=5)).isoformat() + "Z"
    later = (now + timedelta(hours=2)).isoformat() + "Z"
    for account_id, resume_at in (("due", past), ("future", later), ("manual", past)):
        response = await async_client.post(f"/api/accounts/{account_id}/pause")
        assert response.status_code == 200
        response = await async_client.put(f"/api/accounts/{account_id}/resume-at", json={"resumeAt": resume_at})
        assert response.status_code == 200
        assert response.json() == {"accountId": account_id, "resumeAt": resume_at}

    response = await async_client.get("/api/account-resume-schedules")
    assert response.status_code == 200
    assert {row["accountId"] for row in response.json()["schedules"]} == {"due", "future", "manual"}

    response = await async_client.post("/api/accounts/manual/reactivate")
    assert response.status_code == 200
    await resume_due_accounts_once()

    async with SessionLocal() as session:
        assert (await session.get(Account, "due")).status == AccountStatus.ACTIVE
        assert await session.get(AccountResumeSchedule, "due") is None
        assert (await session.get(Account, "future")).status == AccountStatus.PAUSED
        assert (await session.get(AccountResumeSchedule, "future")) is not None
        assert (await session.get(Account, "manual")).status == AccountStatus.ACTIVE
        assert await session.get(AccountResumeSchedule, "manual") is None

    cleared = await async_client.put("/api/accounts/future/resume-at", json={"resumeAt": None})
    assert cleared.status_code == 200
    assert cleared.json() == {"accountId": "future", "resumeAt": None}
    assert (await async_client.get("/api/account-resume-schedules")).json() == {"schedules": []}
    async with SessionLocal() as session:
        assert (await session.get(Account, "future")).status == AccountStatus.PAUSED


@pytest.mark.asyncio
async def test_resume_schedule_unknown_account_returns_404(async_client):
    response = await async_client.put("/api/accounts/missing/resume-at", json={"resumeAt": None})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"
