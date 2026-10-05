"""W1 scenarios (incident 2026-09-30): requests are admitted before they connect, fairly across sessions.

While the upload throttle is on, a request waits for admission before its upstream connection opens.
Admitted-but-unsent bytes stay under a budget, sessions take turns (deficit round robin), and waiting
for admission has its own deadline, separate from the transfer's.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import upload_throttle

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _bulk_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    # Since 2026-10-05 holds and admission apply to bulk transfers only; model requests pass
    # straight through (tests/integration/test_model_upload_never_throttled.py). These cases
    # cover the admission algorithm itself, so they run as bulk uploads.
    monkeypatch.setattr(upload_throttle, "is_bulk", lambda: True)


RATE = 1_000_000  # bytes/s; with the 4 s window the budget is 4 MB
MB = 1_000_000


def _module():
    try:
        from app.core import upload_admission
    except ImportError:  # the module does not exist before W1
        pytest.fail("app.core.upload_admission is missing")
    return upload_admission


@pytest.fixture(autouse=True)
def _throttle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "upload-throttle.json"
    path.write_text(json.dumps({"enabled": True, "bytes_per_sec": RATE}))
    monkeypatch.setenv("AGENT_LB_THROTTLE_FILE", str(path))
    monkeypatch.delenv("AGENT_LB_UPLOAD_FAIR", raising=False)
    return path


async def _hold(admission, session: str, size: int, admitted: list[str], release: asyncio.Event) -> None:
    async with admission.admit(session, size):
        admitted.append(session)
        await release.wait()


@pytest.mark.asyncio
async def test_a_small_session_is_not_starved_behind_a_big_backlog() -> None:
    admission = _module().Admission()
    admitted: list[str] = []
    releases = [asyncio.Event() for _ in range(9)]
    tasks = [asyncio.create_task(_hold(admission, "big", MB, admitted, releases[i])) for i in range(8)]
    await asyncio.sleep(0.2)
    assert admitted == ["big"] * 4  # the 4 MB budget is full
    small = asyncio.create_task(_hold(admission, "small", 20_000, admitted, releases[8]))
    await asyncio.sleep(0.2)
    assert admitted.count("small") == 0
    started = time.monotonic()
    releases[0].set()
    await asyncio.sleep(0.2)
    # One big request finished: the small session goes next, ahead of big's four queued requests.
    assert admitted[4] == "small"
    assert time.monotonic() - started < 1.0
    for event in releases:
        event.set()
    await asyncio.wait_for(asyncio.gather(*tasks, small), timeout=5)
    assert admitted.count("big") == 8


@pytest.mark.asyncio
async def test_a_request_bigger_than_the_budget_goes_alone() -> None:
    admission = _module().Admission()
    admitted: list[str] = []
    release = asyncio.Event()
    task = asyncio.create_task(_hold(admission, "huge", 6 * MB, admitted, release))
    await asyncio.sleep(0.2)
    assert admitted == ["huge"]
    release.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_waiting_for_admission_has_its_own_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setenv("AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS", "0.5")
    admission = module.Admission()
    admitted: list[str] = []
    release = asyncio.Event()
    holders = [asyncio.create_task(_hold(admission, "big", MB, admitted, release)) for _ in range(4)]
    await asyncio.sleep(0.2)
    started = time.monotonic()
    with pytest.raises(module.UploadAdmissionTimeout) as caught:
        async with admission.admit("late", 100_000):
            pass
    waited = time.monotonic() - started
    assert 0.4 <= waited < 2.0
    exc = caught.value
    assert not isinstance(exc, module.UploadAdmissionRejected)
    assert exc.waited_s is not None and exc.waited_s >= 0.4
    assert exc.max_wait_s == 0.5
    assert exc.upload_class == "batch"
    assert "waited" in str(exc)
    release.set()
    await asyncio.wait_for(asyncio.gather(*holders), timeout=2)


@pytest.mark.asyncio
async def test_turning_the_throttle_off_lets_every_waiter_through(_throttle: Path) -> None:
    admission = _module().Admission()
    admitted: list[str] = []
    release = asyncio.Event()
    tasks = [asyncio.create_task(_hold(admission, "big", MB, admitted, release)) for _ in range(6)]
    await asyncio.sleep(0.2)
    assert len(admitted) == 4
    _throttle.write_text(json.dumps({"enabled": False, "bytes_per_sec": RATE}))
    await asyncio.sleep(1.6)
    assert len(admitted) == 6
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)


@pytest.mark.asyncio
async def test_count_tokens_records_a_rejected_upload_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.proxy.anthropic_service import AnthropicProxyError, AnthropicProxyService

    module = _module()
    service = AnthropicProxyService(lambda: None)
    logged: dict[str, object] = {}

    async def select(self, *args, **kwargs):
        del self, args, kwargs
        return SimpleNamespace(id="acct-1")

    async def fresh(self, account, **kwargs):
        del self, account, kwargs
        return "token"

    async def persist(self, **kwargs):
        del self
        logged.update(kwargs)

    monkeypatch.setattr(AnthropicProxyService, "_select_account", select)
    monkeypatch.setattr(AnthropicProxyService, "_fresh_access_token", fresh)
    monkeypatch.setattr(AnthropicProxyService, "_persist_request_log", persist)

    rejected = module.UploadAdmissionRejected(
        rate=200_000,
        backlog_bytes=4_100_000,
        projected_wait_s=21,
        max_wait_s=30,
        upload_class="batch",
    )

    class _Reject:
        async def __aenter__(self):
            raise rejected

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(module.admission(), "admit", lambda *args, **kwargs: _Reject())
    with pytest.raises(AnthropicProxyError) as caught:
        await service.count_tokens(
            {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]},
            {"x-claude-code-session-id": "s1"},
            model="claude-sonnet-4-5",
        )

    assert caught.value.status_code == 503
    assert caught.value.code == "upload_admission_rejected"
    assert logged["error_code"] == "upload_admission_rejected"
    assert logged["status"] == "error"
    message = str(logged["error_message"])
    assert message == caught.value.message
    assert "200 KB/s" in message
    assert "4.1 MB queued ahead" in message
    assert "projected wait 21 s > limit 30 s" in message


@pytest.mark.asyncio
async def test_messages_records_a_rejected_upload_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.anthropic.models import AnthropicMessageRequest
    from app.modules.proxy.anthropic_service import AnthropicProxyError, AnthropicProxyService

    module = _module()
    service = AnthropicProxyService(lambda: None)
    logged: dict[str, object] = {}

    async def select(self, *args, **kwargs):
        del self, args, kwargs
        return SimpleNamespace(id="acct-1")

    async def fresh(self, account, **kwargs):
        del self, account, kwargs
        return "token"

    async def persist(self, **kwargs):
        del self
        logged.update(kwargs)

    async def eligibility(self, *args, **kwargs):
        del self, args, kwargs
        return SimpleNamespace(account_ids=["acct-1"])

    monkeypatch.setattr(AnthropicProxyService, "_select_account", select)
    monkeypatch.setattr(AnthropicProxyService, "_fresh_access_token", fresh)
    monkeypatch.setattr(AnthropicProxyService, "_persist_request_log", persist)
    monkeypatch.setattr(AnthropicProxyService, "_provider_quota_eligibility", eligibility)

    rejected = module.UploadAdmissionRejected(
        rate=200_000,
        backlog_bytes=4_100_000,
        projected_wait_s=21,
        max_wait_s=30,
        upload_class="batch",
    )

    class _Reject:
        async def __aenter__(self):
            raise rejected

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(module.admission(), "admit", lambda *args, **kwargs: _Reject())
    payload = AnthropicMessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=16,
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
    )
    stream = await service.stream_messages(payload, {"x-claude-code-session-id": "s1"})
    with pytest.raises(AnthropicProxyError) as caught:
        async for _chunk in stream.body:
            pass

    assert caught.value.status_code == 503
    assert caught.value.code == "upload_admission_rejected"
    assert logged["error_code"] == "upload_admission_rejected"
    assert logged["status"] == "error"
    message = str(logged["error_message"])
    assert message == caught.value.message
    assert "200 KB/s" in message
    assert "4.1 MB queued ahead" in message
    assert "projected wait 21 s > limit 30 s" in message
