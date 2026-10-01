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


@pytest.mark.asyncio
async def test_same_session_high_is_not_queued_behind_its_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 1.0))
    gate = upload_admission.Admission()
    entered = asyncio.Event()

    async def high() -> None:
        async with gate.admit("session", 4, "high"):
            entered.set()

    async with gate.admit("session", 4, "batch"):
        task = asyncio.create_task(high())
        await asyncio.sleep(0)
        assert entered.is_set()
    await task


@pytest.mark.asyncio
async def test_unknown_upload_class_counts_as_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 250_000))
    gate = upload_admission.Admission()
    entered = {"high": False, "weird": False}

    async def once(name: str, upload_class: str) -> None:
        async with gate.admit(name, 700_000, upload_class):
            entered[name] = True

    async with gate.admit("holder", 700_000, "batch"):
        high = asyncio.create_task(once("high", "high"))
        weird = asyncio.create_task(once("weird", "weird"))
        await asyncio.sleep(0)
        assert entered["high"] is True
        assert entered["weird"] is False
        weird.cancel()
        with pytest.raises(asyncio.CancelledError):
            await weird
    await high


@pytest.mark.asyncio
async def test_anonymous_calls_do_not_share_one_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 250_000))
    gate = upload_admission.Admission()
    admitted: list[int] = []

    async def request(size: int) -> None:
        async with gate.admit(None, size):
            admitted.append(size)

    async with gate.admit(None, 1_000_000):
        large = asyncio.create_task(request(900_000))
        small = asyncio.create_task(request(10))
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(large, small), timeout=1)
    assert admitted == [10, 900_000]


def test_upload_class_header_lookup_is_case_insensitive() -> None:
    from multidict import CIMultiDict

    assert upload_admission.upload_class(CIMultiDict({"User-Agent": "claude-cli/2.1.286 (external, cli)"})) == "high"
    headers = {"User-Agent": "claude-cli/2.1.286 (external, cli)", "X-Claude-Code-Agent-Id": "a1"}
    assert upload_admission.upload_class(headers) == "batch"
    assert upload_admission.upload_class({"x-agent-lb-priority": "  High  "}) == "high"
