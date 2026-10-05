"""Gaming priority scenarios (2026-10-01): high-priority work keeps moving under a gaming hold.

While a throttle hold is on, upload admission has two classes. High (orchestrator and PM tabs,
Recruiter, review and merge traffic) goes first, up to 80% of the budget; batch (fold runners,
seats, evals, explore) gets the rest and may wait, but never stops. With no hold, nothing changes.
The class is resolved by agent-lb from request headers, never from the body.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app.core import upload_throttle

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _bulk_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    # Since 2026-10-05 holds and admission apply to bulk transfers only; model requests pass
    # straight through (tests/integration/test_model_upload_never_throttled.py). These cases
    # cover the admission algorithm itself, so they run as bulk uploads.
    monkeypatch.setattr(upload_throttle, "is_bulk", lambda: True)


RATE = 200_000  # bytes/s, the gaming hold; with the 4 s window the budget is 800 KB
KB = 1_000


def _module():
    from app.core import upload_admission

    return upload_admission


def _write_holds(path: Path, holds: dict) -> None:
    path.write_text(json.dumps({"version": 2, "holds": holds, "rate": RATE}))


@pytest.fixture(autouse=True)
def _throttle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "upload-throttle.json"
    _write_holds(
        path,
        {"p6-gaming": {"bytes_per_sec": RATE, "since": "2026-10-01T13:35:00+00:00", "reason": "League"}},
    )
    monkeypatch.setenv("AGENT_LB_THROTTLE_FILE", str(path))
    monkeypatch.delenv("AGENT_LB_UPLOAD_FAIR", raising=False)
    return path


class _Uploads:
    """Admitted requests stay open until released, like a body still uploading."""

    def __init__(self, admission) -> None:
        self.admission = admission
        self.admitted: list[str] = []
        self.open: list[asyncio.Event] = []
        self.tasks: list[asyncio.Task] = []

    def submit(self, session: str, size: int, upload_class: str) -> None:
        async def run() -> None:
            async with self.admission.admit(session, size, upload_class=upload_class):
                release = asyncio.Event()
                self.open.append(release)
                self.admitted.append(session)
                await release.wait()

        self.tasks.append(asyncio.create_task(run()))

    def finish_oldest(self) -> None:
        self.open.pop(0).set()

    async def drain(self) -> None:
        deadline = time.monotonic() + 10
        while not all(task.done() for task in self.tasks):
            assert time.monotonic() < deadline, f"uploads stuck; admitted so far {self.admitted}"
            if self.open:
                self.finish_oldest()
            await asyncio.sleep(0.02)
        for task in self.tasks:
            task.result()


@pytest.mark.asyncio
async def test_a_high_request_goes_ahead_of_a_batch_backlog() -> None:
    uploads = _Uploads(_module().Admission())
    for _ in range(6):
        uploads.submit("fold-runner-1", 300 * KB, "batch")
        uploads.submit("fold-runner-2", 300 * KB, "batch")
    await asyncio.sleep(0.2)
    assert 1 <= len(uploads.admitted) <= 3  # the two batch flows fill the 800 KB budget
    batch_before = len(uploads.admitted)

    started = time.monotonic()
    uploads.submit("orchestrator", 1_000 * KB, "high")
    # Batch uploads keep finishing, one every 0.3 s, as they would at 0.2 MB/s.
    while "orchestrator" not in uploads.admitted:
        assert time.monotonic() - started < 6.0, f"high request still waiting; admitted {uploads.admitted}"
        await asyncio.sleep(0.3)
        if "orchestrator" not in uploads.admitted and uploads.open:
            uploads.finish_oldest()
        await asyncio.sleep(0.05)
    # No batch request queued behind it was admitted while the high request waited.
    assert uploads.admitted.index("orchestrator") == batch_before

    await uploads.drain()
    assert uploads.admitted.count("fold-runner-1") == 6
    assert uploads.admitted.count("fold-runner-2") == 6


@pytest.mark.asyncio
async def test_batch_still_moves_under_a_steady_high_backlog() -> None:
    uploads = _Uploads(_module().Admission())
    for _ in range(8):
        uploads.submit("pm-tab", 300 * KB, "high")
    for _ in range(3):
        uploads.submit("eval-seat", 300 * KB, "batch")
    await asyncio.sleep(0.2)
    await uploads.drain()
    order = uploads.admitted
    assert order.count("pm-tab") == 8 and order.count("eval-seat") == 3
    last_high = len(order) - 1 - order[::-1].index("pm-tab")
    # Batch gets its share while high work is still queued, instead of waiting for all of it.
    assert order.index("eval-seat") < last_high


@pytest.mark.asyncio
async def test_without_a_hold_the_class_changes_nothing(_throttle: Path) -> None:
    _write_holds(_throttle, {})
    uploads = _Uploads(_module().Admission())
    for _ in range(4):
        uploads.submit("fold-runner-1", 2_000 * KB, "batch")
        uploads.submit("orchestrator", 2_000 * KB, "high")
    await asyncio.sleep(0.2)
    # Throttle off: everything is admitted at once, as before classes existed.
    assert len(uploads.admitted) == 8
    await uploads.drain()


def test_the_class_comes_from_headers_resolved_by_agent_lb() -> None:
    upload_class = _module().upload_class
    tab = {"user-agent": "claude-cli/2.1.286 (external, cli)", "x-claude-code-session-id": "s1"}
    assert upload_class(tab) == "high"
    assert upload_class({**tab, "x-claude-code-agent-id": "a1"}) == "batch"  # a subagent (seat) of that tab
    assert upload_class({"user-agent": "claude-cli/2.1.286 (external, sdk-cli)"}) == "batch"  # headless -p
    assert upload_class({"user-agent": "claude-cli/2.1.286 (external, cli, workload/cron)"}) == "batch"
    assert upload_class({"user-agent": "Python-urllib/3.14"}) == "batch"
    assert upload_class({}) == "batch"
    # An explicit priority header (review_pr, queue_pr, Recruiter) wins, either way, case-insensitively.
    headless = {"user-agent": "claude-cli/2.1.286 (external, sdk-cli)"}
    assert upload_class({**headless, "X-Agent-LB-Priority": "high"}) == "high"
    assert upload_class({**tab, "x-agent-lb-priority": "batch"}) == "batch"
    assert upload_class({**tab, "x-agent-lb-priority": "urgent"}) == "high"  # unknown values are ignored
