from __future__ import annotations

import asyncio

import pytest

from app.core import upload_admission

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_fitting_session_earns_credit_before_last_session_is_served_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 250_000))
    gate = upload_admission.Admission()
    admitted: list[str] = []

    async def request(session: str, size: int) -> None:
        async with gate.admit(session, size):
            admitted.append(session)

    async with gate.admit("backlog", 1_000_000):
        backlog = asyncio.create_task(request("backlog", 10))
        other = asyncio.create_task(request("other", 900_000))
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(backlog, other), timeout=1)
    assert admitted == ["other", "backlog"]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_block_its_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 1.0))
    gate = upload_admission.Admission()
    entered = asyncio.Event()

    async def wait() -> None:
        async with gate.admit("queued", 4):
            entered.set()

    async with gate.admit("holder", 4):
        task = asyncio.create_task(wait())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not entered.is_set()
    async with asyncio.timeout(0.5):
        async with gate.admit("queued", 4):
            pass


@pytest.mark.asyncio
async def test_exception_releases_budget_and_rollback_bypasses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 1.0))
    gate = upload_admission.Admission()
    with pytest.raises(RuntimeError):
        async with gate.admit("failed", 4):
            raise RuntimeError("transfer failed")
    async with asyncio.timeout(0.5):
        async with gate.admit("holder", 4):
            monkeypatch.setenv("AGENT_LB_UPLOAD_FAIR", "0")
            async with gate.admit("rollback", 4):
                pass
