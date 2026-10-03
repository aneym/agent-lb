from __future__ import annotations

import asyncio
import logging
import time
from hashlib import sha256

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


@pytest.mark.asyncio
async def test_hopeless_queue_is_rejected_before_the_wait_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 1_000))
    monkeypatch.setenv("AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS", "30")
    gate = upload_admission.Admission()
    session = "seat-secret-do-not-log"

    async with gate.admit("holder", 100_000):
        started = time.monotonic()
        with caplog.at_level(logging.WARNING):
            with pytest.raises(upload_admission.UploadAdmissionRejected) as caught:
                async with gate.admit(session, 100_000):
                    pass
        elapsed = time.monotonic() - started

    assert elapsed < 0.5
    exc = caught.value
    assert exc.rate == 1_000
    assert exc.backlog_bytes == 100_000
    assert exc.projected_wait_s > 30
    assert exc.max_wait_s == 30
    assert exc.upload_class == "batch"
    assert "1 KB/s" in str(exc)
    assert "0.1 MB queued ahead" in str(exc)
    assert "projected wait" in str(exc)
    assert session not in caplog.text
    assert sha256(session.encode()).hexdigest()[:12] in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("holders", "new"),
    [
        ([("h1", 100_000, "high")], ("s2", 8_000_000, "high")),
        ([("h1", 7_000_000, "high"), ("b1", 100_000, "batch")], ("s2", 100_000, "batch")),
    ],
)
async def test_queue_that_drains_inside_the_ceiling_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
    holders: list[tuple[str, int, str]],
    new: tuple[str, int, str],
) -> None:
    monkeypatch.setattr(upload_admission.upload_throttle, "read_state", lambda: (True, 200_000))
    monkeypatch.setenv("AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS", "30")
    gate = upload_admission.Admission()
    release = asyncio.Event()

    async def hold(session: str, nbytes: int, upload_class: str) -> None:
        async with gate.admit(session, nbytes, upload_class):
            await release.wait()

    tasks = [asyncio.create_task(hold(*holder)) for holder in holders]
    await asyncio.sleep(0.05)

    async def release_soon() -> None:
        await asyncio.sleep(1.0)
        release.set()

    releaser = asyncio.create_task(release_soon())
    started = time.monotonic()
    async with gate.admit(*new):
        pass
    assert time.monotonic() - started < 5
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks, releaser), timeout=2)


def test_upload_class_header_lookup_is_case_insensitive() -> None:
    from multidict import CIMultiDict

    assert upload_admission.upload_class(CIMultiDict({"User-Agent": "claude-cli/2.1.286 (external, cli)"})) == "high"
    headers = {"User-Agent": "claude-cli/2.1.286 (external, cli)", "X-Claude-Code-Agent-Id": "a1"}
    assert upload_admission.upload_class(headers) == "batch"
    assert upload_admission.upload_class({"x-agent-lb-priority": "  High  "}) == "high"


def test_sdk_ts_and_agent_sdk_are_batch_unless_priority_is_high() -> None:
    sdk_ts = "claude-cli/2.1.288 (external, sdk-ts, agent-sdk/0.3.288)"
    agent_sdk = "claude-cli/2.1.288 (external, agent-sdk/0.3.288)"
    tui = "claude-cli/2.1.288 (external, cli)"
    assert upload_admission.upload_class({"user-agent": sdk_ts}) == "batch"
    assert upload_admission.upload_class({"user-agent": agent_sdk}) == "batch"
    assert upload_admission.upload_class({"user-agent": sdk_ts, "x-agent-lb-priority": "high"}) == "high"
    assert upload_admission.upload_class({"user-agent": tui}) == "high"
