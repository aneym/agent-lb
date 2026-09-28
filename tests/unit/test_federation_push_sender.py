"""Scenario tests for the federation push SENDER (openspec add-federation-account-push).

The sender owns and refreshes its accounts and pushes access tokens only. These tests drive the
fixed seams: load_push_targets, build_push_request, FederationPushScheduler.push_once,
AiohttpFederationPeerClient.push_accounts and the `push` CLI main().
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import app.modules.federation.push as push_module
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.federation.exceptions import FederationPeerRequestError
from app.modules.federation.peer_client import AiohttpFederationPeerClient
from app.modules.federation.push import (
    FederationPushScheduler,
    PushTarget,
    build_push_request,
    load_push_targets,
    main,
)
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import (
    FederationMirrorAccount,
    FederationPushRequest,
    FederationPushResponse,
    FederationPushSkip,
    FederationUsageDayRollup,
)
from app.modules.federation.service import FederationService

pytestmark = pytest.mark.unit

_LOCAL = "nate-sender-unit-test"
_OTHER = "laptop-owner-unit-test"

# Every token the seeds write. None may ever appear in CLI output or in a response record,
# and no refresh token may ever appear in a push payload.
_ACCESS = {
    "acc_push_a": "access-token-a-DO-NOT-LEAK",
    "acc_push_b": "access-token-b-DO-NOT-LEAK",
    "acc_push_m": "access-token-m-DO-NOT-LEAK",
}
_REFRESH = {
    "acc_push_a": "refresh-token-a-DO-NOT-LEAK",
    "acc_push_b": "refresh-token-b-DO-NOT-LEAK",
    "acc_push_m": "refresh-token-m-DO-NOT-LEAK",
}
_ALL_TOKENS = [*_ACCESS.values(), *_REFRESH.values()]


def _account(account_id: str, *, email: str, provider: str, owner_instance: str | None) -> Account:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider=provider,
        chatgpt_account_id="chatgpt-b" if provider == "openai" else None,
        email=email,
        alias=f"alias-{account_id}",
        plan_type="plus" if provider == "openai" else "claude",
        access_token_encrypted=encryptor.encrypt(_ACCESS[account_id]),
        refresh_token_encrypted=encryptor.encrypt(_REFRESH[account_id]),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.access_expires_at = utcnow() + timedelta(hours=1)
    account.owner_instance = owner_instance
    return account


async def _seed_owned_and_mirrored() -> None:
    """a (owner NULL = local) and b (owner = local) are owned; m is mirrored from another LB."""
    async with SessionLocal() as session:
        session.add(_account("acc_push_a", email="a@example.com", provider="anthropic", owner_instance=None))
        session.add(_account("acc_push_b", email="b@example.com", provider="openai", owner_instance=_LOCAL))
        session.add(_account("acc_push_m", email="m@example.com", provider="anthropic", owner_instance=_OTHER))
        await session.commit()


def _write_targets(path: Path, targets: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"targets": targets}))
    return path


@asynccontextmanager
async def _db_repo_factory() -> AsyncIterator[FederationRepository]:
    async with SessionLocal() as session:
        yield FederationRepository(session)


def _response(*, source: str = "nate", accepted: list[str] | None = None, usage=None) -> FederationPushResponse:
    return FederationPushResponse(
        source=source,
        accepted=accepted or [],
        skipped=[],
        removed=[],
        usage=usage or [],
    )


class _RecordingPeer:
    """Stands in for the network only. Fails for URLs listed in `failing`."""

    def __init__(self, *, responses: dict[str, FederationPushResponse] | None = None) -> None:
        self.responses = responses or {}
        self.failing: set[str] = set()
        self.calls: list[tuple[str, FederationPushRequest]] = []

    async def push_accounts(self, *, url: str, request: FederationPushRequest) -> FederationPushResponse:
        self.calls.append((url, request))
        if url in self.failing:
            raise FederationPeerRequestError("Federation peer request failed status=503", status_code=503)
        return self.responses.get(url, _response())

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


async def _no_sleep(_seconds: float) -> None:
    return None


def _scheduler(
    *, path: Path, peer: _RecordingPeer, interval_seconds: int = 300, repo_factory=_db_repo_factory
) -> FederationPushScheduler:
    return FederationPushScheduler(
        interval_seconds=interval_seconds,
        path=path,
        local_instance_id=_LOCAL,
        repo_factory=repo_factory,
        peer_client=peer,
        encryptor=TokenEncryptor(),
        sleep=_no_sleep,
    )


# --- load_push_targets -------------------------------------------------------------------------


def test_load_push_targets_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_push_targets(tmp_path / "absent.json") == []


@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps({"targets": "not-a-list"}), json.dumps({"targets": [{"name": "x"}]})],
    ids=["invalid-json", "targets-not-a-list", "target-missing-url"],
)
def test_load_push_targets_malformed_is_empty_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
) -> None:
    path = tmp_path / "federation-push.json"
    path.write_text(content)

    with caplog.at_level(logging.WARNING):
        assert load_push_targets(path) == []

    assert any(record.levelno >= logging.WARNING for record in caplog.records)


def test_load_push_targets_valid_file(tmp_path: Path) -> None:
    path = _write_targets(
        tmp_path / "federation-push.json",
        [
            {"name": "alex-studio", "url": "https://studio.example", "accounts": ["a@example.com", "acc_push_b"]},
            {"name": "spare", "url": "https://spare.example", "accounts": []},
        ],
    )

    assert load_push_targets(path) == [
        PushTarget(name="alex-studio", url="https://studio.example", accounts=("a@example.com", "acc_push_b")),
        PushTarget(name="spare", url="https://spare.example", accounts=()),
    ]


# --- build_push_request ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_push_request_sends_only_owned_matched_accounts_without_refresh_tokens(db_setup: bool) -> None:
    """Spec scenario: Only owned, matched accounts are sent."""
    del db_setup
    await _seed_owned_and_mirrored()
    target = PushTarget(
        name="alex-studio",
        url="https://studio.example",
        accounts=("A@EXAMPLE.COM", "acc_push_b", "m@example.com", "nobody@example.com"),
    )
    encryptor = TokenEncryptor()

    async with SessionLocal() as session:
        repo = FederationRepository(session)
        request, unmatched = await build_push_request(repo, target, local_instance_id=_LOCAL, encryptor=encryptor)
        mirror_export = await FederationService(
            repo, settings=Settings(local_instance_id=_LOCAL), encryptor=encryptor
        ).build_mirror_response()

    assert request.instance_id == _LOCAL
    by_id = {account.account_id: account for account in request.accounts}
    assert sorted(by_id) == ["acc_push_a", "acc_push_b"]
    # Each pushed account is exactly what the owner's own mirror export says about it.
    exported: dict[str, FederationMirrorAccount] = {account.account_id: account for account in mirror_export.accounts}
    assert by_id["acc_push_a"] == exported["acc_push_a"]
    assert by_id["acc_push_b"] == exported["acc_push_b"]
    assert by_id["acc_push_a"].access_token == _ACCESS["acc_push_a"]
    assert by_id["acc_push_a"].expires_at_ms is not None

    assert sorted(unmatched) == ["m@example.com", "nobody@example.com"]

    serialized = request.model_dump_json()
    for refresh_token in _REFRESH.values():
        assert refresh_token not in serialized
    assert "refresh_token" not in serialized
    assert _ACCESS["acc_push_m"] not in serialized


# --- FederationPushScheduler.push_once ---------------------------------------------------------


@pytest.mark.asyncio
async def test_push_once_without_config_sends_nothing(tmp_path: Path) -> None:
    """Spec scenario: No config file, no pushes."""
    peer = _RecordingPeer()
    scheduler = _scheduler(path=tmp_path / "absent.json", peer=peer)

    assert await scheduler.push_once() == {}
    assert peer.calls == []


@pytest.mark.asyncio
async def test_push_once_stores_returned_usage_under_target_name(db_setup: bool, tmp_path: Path) -> None:
    """Spec scenario: Returned usage is stored under the target name."""
    del db_setup
    await _seed_owned_and_mirrored()
    url = "https://studio.example"
    path = _write_targets(
        tmp_path / "federation-push.json", [{"name": "alex-studio", "url": url, "accounts": ["a@example.com"]}]
    )
    rollup = FederationUsageDayRollup(
        day=date.today(),
        account_id="acc_push_a",
        provider="anthropic",
        requests=7,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cost=0.25,
        session_count=2,
        last_request_at=None,
    )
    peer = _RecordingPeer(responses={url: _response(accepted=["acc_push_a"], usage=[rollup])})
    scheduler = _scheduler(path=path, peer=peer)

    results = await scheduler.push_once()

    assert peer.urls() == [url]
    sent = peer.calls[0][1]
    assert [account.account_id for account in sent.accounts] == ["acc_push_a"]
    assert isinstance(results["alex-studio"], FederationPushResponse)
    async with SessionLocal() as session:
        stored = await FederationRepository(session).list_stored_usage_rollups(window_days=7)
    assert [(row.instance_id, row.rollup.account_id, row.rollup.requests) for row in stored] == [
        ("alex-studio", "acc_push_a", 7)
    ]
    assert "alex-studio" in scheduler.last_results
    assert "acc_push_a" in str(scheduler.last_results["alex-studio"])
    for token in _ALL_TOKENS:
        assert token not in str(scheduler.last_results)


class _EmptyOwnedRepo:
    """Scheduler-timing tests need no accounts; the real repository is covered above."""

    async def list_locally_owned_accounts(self, local_instance_id: str) -> list[Account]:
        del local_instance_id
        return []

    async def upsert_usage_report(self, instance_id, rollups, *, reported_at) -> None:
        del instance_id, rollups, reported_at


@asynccontextmanager
async def _empty_repo_factory() -> AsyncIterator[_EmptyOwnedRepo]:
    yield _EmptyOwnedRepo()


class _FakeClock:
    """Moves every clock the push module could read (wall, monotonic, loop time, utcnow) together."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._offset = 0.0
        real_time, real_monotonic = time.time, time.monotonic
        base_wall, base_mono = real_time(), real_monotonic()
        clock = self

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return datetime.fromtimestamp(base_wall + clock._offset, tz=tz)

            @classmethod
            def utcnow(cls):  # type: ignore[override]
                return datetime.fromtimestamp(base_wall + clock._offset, tz=timezone.utc).replace(tzinfo=None)

        monkeypatch.setattr(time, "time", lambda: base_wall + clock._offset)
        monkeypatch.setattr(time, "monotonic", lambda: base_mono + clock._offset)
        monkeypatch.setattr(push_module, "utcnow", lambda: _FrozenDatetime.utcnow(), raising=False)
        if getattr(push_module, "datetime", None) is datetime:
            monkeypatch.setattr(push_module, "datetime", _FrozenDatetime)

    def advance(self, seconds: float) -> None:
        self._offset += seconds

    def now(self) -> float:
        return self._offset


class _ScriptedPeer:
    """t1 fails on every attempt except those listed in `t1_successes`; t2 always succeeds.

    Records the fake-clock time of each attempt per URL.
    """

    def __init__(self, clock: _FakeClock, *, t1: str, t1_successes: set[int]) -> None:
        self.clock = clock
        self.t1 = t1
        self.t1_successes = t1_successes
        self.attempts: dict[str, list[float]] = {}

    async def push_accounts(self, *, url: str, request: FederationPushRequest) -> FederationPushResponse:
        del request
        times = self.attempts.setdefault(url, [])
        times.append(self.clock.now())
        if url == self.t1 and len(times) - 1 not in self.t1_successes:
            raise FederationPeerRequestError("Federation peer request failed status=503", status_code=503)
        return _response()


@pytest.mark.asyncio
async def test_failing_target_backs_off_exponentially_capped_without_holding_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec scenario: A failing target backs off without holding others.

    Driven through the background loop (start/stop) with the injected sleep moving a fake clock.
    interval_seconds is 30, so the spec's min(1800, interval * 2^(n-1)) and a 30 s base agree.
    Each cycle sleeps interval + 10 ms, so a retry lands on the first cycle after its backoff
    whether the boundary check is < or <=.
    """
    interval = 30
    t1, t2 = "https://t1.example", "https://t2.example"
    path = _write_targets(
        tmp_path / "federation-push.json",
        [{"name": "t1", "url": t1, "accounts": []}, {"name": "t2", "url": t2, "accounts": []}],
    )
    clock = _FakeClock(monkeypatch)
    # Attempt 9 succeeds; every other t1 attempt fails.
    peer = _ScriptedPeer(clock, t1=t1, t1_successes={9})
    cycles_wanted = 300
    cycles_run = 0
    done = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        nonlocal cycles_run
        cycles_run += 1
        clock.advance(seconds + 0.01)
        if cycles_run >= cycles_wanted:
            done.set()
            await asyncio.Event().wait()

    scheduler = FederationPushScheduler(
        interval_seconds=interval,
        path=path,
        local_instance_id=_LOCAL,
        repo_factory=_empty_repo_factory,
        peer_client=peer,
        encryptor=TokenEncryptor(),
        sleep=fake_sleep,
    )
    await scheduler.start()
    try:
        await done.wait()
    finally:
        await scheduler.stop()

    # t2 is pushed every cycle regardless of t1.
    t2_times = peer.attempts[t2]
    assert len(t2_times) >= cycles_wanted
    assert max(b - a for a, b in zip(t2_times, t2_times[1:])) < interval + 1

    t1_times = peer.attempts[t1]
    gaps = [b - a for a, b in zip(t1_times, t1_times[1:])]
    # Minimum wait after attempt k: failures 1..9 back off 30, 60, ... capped at 1800;
    # attempt 9 succeeds (next cycle); attempts 10 and 11 fail again with a reset streak.
    expected_backoff = [30, 60, 120, 240, 480, 960, 1800, 1800, 1800, 0, 30, 60]
    assert len(gaps) >= len(expected_backoff), f"t1 attempted only {len(t1_times)} times"
    for k, backoff in enumerate(expected_backoff):
        assert backoff <= gaps[k] < backoff + interval + 1, (
            f"t1 attempt {k + 1} came {gaps[k]:.2f}s after attempt {k}; expected the first cycle after {backoff}s"
        )
    assert isinstance(scheduler.last_results.get("t1"), dict) and scheduler.last_results["t1"]


# --- AiohttpFederationPeerClient.push_accounts -------------------------------------------------


@pytest.mark.asyncio
async def test_peer_client_push_accounts_posts_to_push_endpoint() -> None:
    received: list[tuple[str, dict[str, object], str | None]] = []
    reply = _response(accepted=["acc_push_a"])

    async def handler(request: web.Request) -> web.Response:
        received.append((request.path, await request.json(), request.headers.get("Authorization")))
        return web.json_response(reply.model_dump(mode="json"))

    app = web.Application()
    app.router.add_post("/api/federation/push", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        push_request = FederationPushRequest(
            instance_id=_LOCAL,
            accounts=[
                FederationMirrorAccount(
                    account_id="acc_push_a",
                    provider="anthropic",
                    email="a@example.com",
                    status="active",
                    plan_type="claude",
                    access_token=_ACCESS["acc_push_a"],
                )
            ],
        )
        response = await AiohttpFederationPeerClient().push_accounts(
            url=str(server.make_url("")).rstrip("/"), request=push_request
        )
    finally:
        await server.close()

    assert response == reply
    assert len(received) == 1
    path, body, authorization = received[0]
    assert path == "/api/federation/push"
    assert FederationPushRequest.model_validate(body) == push_request
    assert authorization is None


@pytest.mark.asyncio
async def test_peer_client_push_accounts_raises_on_non_2xx_without_leaking_body() -> None:
    async def handler(request: web.Request) -> web.Response:
        del request
        return web.json_response({"detail": f"echo {_ACCESS['acc_push_a']}"}, status=409)

    app = web.Application()
    app.router.add_post("/api/federation/push", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        with pytest.raises(Exception) as raised:
            await AiohttpFederationPeerClient().push_accounts(
                url=str(server.make_url("")).rstrip("/"),
                request=FederationPushRequest(instance_id=_LOCAL, accounts=[]),
            )
    finally:
        await server.close()

    assert "409" in str(raised.value)
    assert _ACCESS["acc_push_a"] not in str(raised.value)


# --- CLI: python -m app.modules.federation.push status|once ------------------------------------


@pytest.fixture
def cli_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "federation-push.json"
    monkeypatch.setenv("AGENT_LB_FEDERATION_PUSH_PATH", str(path))
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", _LOCAL)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_cli_status_lists_matches_and_unmatched_without_tokens(
    db_setup: bool, cli_settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec scenario: Status shows matches without tokens."""
    del db_setup
    asyncio.run(_seed_owned_and_mirrored())
    _write_targets(
        cli_settings,
        [
            {
                "name": "alex-studio",
                "url": "https://studio.example",
                "accounts": ["A@example.com", "acc_push_b", "nobody@example.com"],
            }
        ],
    )

    assert main(["status"]) == 0

    out = capsys.readouterr()
    text = out.out + out.err
    for expected in (
        "alex-studio",
        "https://studio.example",
        "a@example.com",
        "acc_push_a",
        "anthropic",
        "b@example.com",
        "acc_push_b",
        "openai",
        "nobody@example.com",
    ):
        assert expected in text
    for token in _ALL_TOKENS:
        assert token not in text


def test_cli_once_reports_each_target_and_exits_1_on_failure(
    db_setup: bool, cli_settings: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec scenario: Once reports per-target results and failures."""
    del db_setup
    asyncio.run(_seed_owned_and_mirrored())
    good, bad = "https://good.example", "https://bad.example"
    _write_targets(
        cli_settings,
        [
            {"name": "good-target", "url": good, "accounts": ["a@example.com", "b@example.com"]},
            {"name": "bad-target", "url": bad, "accounts": ["a@example.com"]},
        ],
    )
    sent: list[tuple[str, FederationPushRequest]] = []

    async def fake_push_accounts(self, *, url: str, request: FederationPushRequest) -> FederationPushResponse:
        del self
        sent.append((url, request))
        if url.rstrip("/") == bad:
            raise FederationPeerRequestError("Federation peer request failed status=502", status_code=502)
        return FederationPushResponse(
            source="nate",
            accepted=["acc_push_a"],
            skipped=[FederationPushSkip(account_id="acc_push_b", reason="expired")],
            removed=["acc_push_gone"],
            usage=[],
        )

    monkeypatch.setattr(AiohttpFederationPeerClient, "push_accounts", fake_push_accounts)

    assert main(["once"]) == 1

    assert sorted(url.rstrip("/") for url, _ in sent) == [bad, good]
    good_request = next(request for url, request in sent if url.rstrip("/") == good)
    assert sorted(account.account_id for account in good_request.accounts) == ["acc_push_a", "acc_push_b"]
    out = capsys.readouterr()
    text = out.out + out.err
    for expected in ("good-target", "acc_push_a", "acc_push_b", "expired", "acc_push_gone", "bad-target"):
        assert expected in text
    # The operator needs the peer's HTTP status (403 whois refusal, 409 binding conflict, 5xx down).
    # FederationPeerRequestError.status_code is an int the client sets, never response body text.
    assert "502" in text, "once must print the failed target's HTTP status code"
    for token in _ALL_TOKENS:
        assert token not in text
