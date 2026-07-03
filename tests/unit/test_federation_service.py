from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountTransferDirection, AccountTransferState
from app.db.session import SessionLocal
from app.modules.federation.exceptions import FederationConflictError
from app.modules.federation.peer_client import CheckinPeerResult, CheckoutPeerResult
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import FederationAuthPayload
from app.modules.federation.service import FederationService

pytestmark = pytest.mark.unit

_LOCAL_INSTANCE_ID = "studio-unit-test"
_OWNER_INSTANCE_ID = "studio-owner-unit-test"


@dataclass
class _FakePeerClient:
    checkout_result: CheckoutPeerResult | None = None
    checkout_confirm_error: Exception | None = None
    checkin_result: CheckinPeerResult | None = None
    checkin_observer: Callable[[str, str, FederationAuthPayload], Awaitable[None]] | None = None
    confirm_calls: list[str] | None = None
    checkin_calls: list[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        self.confirm_calls = []
        self.checkin_calls = []

    async def fetch_mirror(self, *, peer_url: str, token: str) -> Any:
        raise NotImplementedError

    async def checkout(
        self, *, peer_url: str, token: str, account_id: str, taker_instance_id: str
    ) -> CheckoutPeerResult:
        assert self.checkout_result is not None
        return self.checkout_result

    async def checkout_confirm(self, *, peer_url: str, token: str, nonce: str) -> None:
        assert self.confirm_calls is not None
        self.confirm_calls.append(nonce)
        if self.checkout_confirm_error is not None:
            raise self.checkout_confirm_error

    async def checkin(
        self,
        *,
        peer_url: str,
        token: str,
        account_id: str,
        nonce: str,
        auth: FederationAuthPayload,
    ) -> CheckinPeerResult:
        assert self.checkin_calls is not None
        self.checkin_calls.append((account_id, nonce))
        if self.checkin_observer is not None:
            await self.checkin_observer(account_id, nonce, auth)
        assert self.checkin_result is not None
        return self.checkin_result


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "local_instance_id": _LOCAL_INSTANCE_ID,
        "federation_peer_url": "https://owner.example.internal",
        "federation_token": "peer-secret",
    }
    defaults.update(overrides)
    return Settings(**defaults)


async def _seed_account(account_id: str, *, owner_instance: str | None) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider="anthropic",
        chatgpt_account_id=None,
        email=f"{account_id}@example.com",
        alias=None,
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt("seed-access"),
        refresh_token_encrypted=encryptor.encrypt("seed-refresh"),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner_instance
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()


async def _get_account(account_id: str) -> Account:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        return account


def _checkout_result(*, nonce: str = "peer-nonce-1") -> CheckoutPeerResult:
    return CheckoutPeerResult(
        nonce=nonce,
        owner_instance_id=_OWNER_INSTANCE_ID,
        auth=FederationAuthPayload(
            access_token="peer-access",
            refresh_token="peer-refresh",
            id_token=None,
            expires_at_ms=None,
            provider="anthropic",
            email="acc_execute@example.com",
            alias=None,
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
        ),
    )


@pytest.mark.asyncio
async def test_execute_checkout_happy_path(db_setup: bool) -> None:
    del db_setup
    peer = _FakePeerClient(checkout_result=_checkout_result(), checkin_result=None)
    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=_settings(), peer_client=peer)
        result = await service.execute_checkout("acc_execute")

    assert result.confirmed is True
    assert result.owner_instance == _LOCAL_INSTANCE_ID
    assert peer.confirm_calls == [result.nonce]

    account = await _get_account("acc_execute")
    assert account.owner_instance == _LOCAL_INSTANCE_ID
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(account.refresh_token_encrypted) == "peer-refresh"

    async with SessionLocal() as session:
        transfer = await FederationRepository(session).get_transfer_by_nonce(result.nonce)
    assert transfer is not None
    assert transfer.state == AccountTransferState.SETTLED


@pytest.mark.asyncio
async def test_execute_checkout_confirm_failure_leaves_account_owned_and_unconfirmed(db_setup: bool) -> None:
    del db_setup
    peer = _FakePeerClient(
        checkout_result=_checkout_result(nonce="peer-nonce-2"),
        checkout_confirm_error=RuntimeError("peer unreachable"),
    )
    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=_settings(), peer_client=peer)
        result = await service.execute_checkout("acc_execute_unconfirmed")

    # The confirm step failed, but the account is still safely owned locally
    # and the failure is surfaced rather than raised.
    assert result.confirmed is False
    assert result.owner_instance == _LOCAL_INSTANCE_ID
    assert peer.confirm_calls == [result.nonce]

    account = await _get_account("acc_execute_unconfirmed")
    assert account.owner_instance == _LOCAL_INSTANCE_ID

    async with SessionLocal() as session:
        transfer = await FederationRepository(session).get_transfer_by_nonce(result.nonce)
    assert transfer is not None
    assert transfer.state == AccountTransferState.PENDING
    assert transfer.direction == AccountTransferDirection.CHECKOUT
    assert transfer.counterparty_instance_id == _OWNER_INSTANCE_ID


@pytest.mark.asyncio
async def test_execute_checkin_closes_local_gate_before_calling_peer(db_setup: bool) -> None:
    del db_setup
    await _seed_account("acc_checkin_execute", owner_instance=_LOCAL_INSTANCE_ID)

    # Establish the counterparty (original owner) via a prior local checkout
    # transfer record, matching how execute_checkout would have left it.
    async with SessionLocal() as session:
        await FederationRepository(session).create_transfer(
            account_id="acc_checkin_execute",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id=_OWNER_INSTANCE_ID,
            nonce="prior-checkout-nonce",
        )

    owner_instance_when_peer_called: list[str | None] = []

    async def _observe(account_id: str, nonce: str, auth: FederationAuthPayload) -> None:
        del nonce, auth
        account = await _get_account(account_id)
        owner_instance_when_peer_called.append(account.owner_instance)

    peer = _FakePeerClient(checkin_result=CheckinPeerResult(settled=True), checkin_observer=_observe)
    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=_settings(), peer_client=peer)
        result = await service.execute_checkin("acc_checkin_execute")

    assert result.settled is True
    # The local gate (owner_instance) must already be the counterparty by the
    # time the peer call happens — never unilaterally reclaimed after.
    assert owner_instance_when_peer_called == [_OWNER_INSTANCE_ID]
    account = await _get_account("acc_checkin_execute")
    assert account.owner_instance == _OWNER_INSTANCE_ID


@pytest.mark.asyncio
async def test_checkout_race_exactly_one_taker_wins(db_setup: bool) -> None:
    del db_setup
    await _seed_account("acc_checkout_race", owner_instance=None)

    async def _attempt(taker: str) -> object:
        try:
            async with SessionLocal() as session:
                service = FederationService(FederationRepository(session), settings=_settings())
                return await service.checkout("acc_checkout_race", taker)
        except FederationConflictError as exc:
            return exc

    results = await asyncio.gather(_attempt("taker-race-a"), _attempt("taker-race-b"))
    outcomes = dict(zip(("taker-race-a", "taker-race-b"), results, strict=True))

    successes = {taker: r for taker, r in outcomes.items() if not isinstance(r, FederationConflictError)}
    failures = {taker: r for taker, r in outcomes.items() if isinstance(r, FederationConflictError)}

    # The guarded compare-and-set UPDATE means exactly one concurrent
    # checkout can ever win the account; the other must hit the conflict
    # path (409), never both succeeding (double owner).
    assert len(successes) == 1
    assert len(failures) == 1

    winning_taker = next(iter(successes))
    account = await _get_account("acc_checkout_race")
    assert account.owner_instance == winning_taker


@pytest.mark.asyncio
async def test_double_checkout_checkin_round_trip_reimports_on_second_checkin(db_setup: bool) -> None:
    del db_setup
    account_id = "acc_double_round_trip"
    taker = "taker-round-trip"
    owner_settings = _settings(local_instance_id=_OWNER_INSTANCE_ID)
    await _seed_account(account_id, owner_instance=None)

    async def _round_trip(*, access_token: str, refresh_token: str, checkin_nonce: str) -> None:
        async with SessionLocal() as session:
            service = FederationService(FederationRepository(session), settings=owner_settings)
            checkout_response = await service.checkout(account_id, taker)
            await service.confirm_checkout(checkout_response.nonce)
            checkin_response = await service.checkin(
                account_id,
                checkin_nonce,
                FederationAuthPayload(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    id_token=None,
                    expires_at_ms=None,
                    provider="anthropic",
                    email=f"{account_id}@example.com",
                    alias=None,
                    status="active",
                    plan_type="claude",
                    chatgpt_account_id=None,
                ),
            )
            assert checkin_response.state == "settled"

    # Round 1: a correctly-behaving taker mints a fresh checkin nonce.
    await _round_trip(
        access_token="round1-access", refresh_token="round1-refresh", checkin_nonce="checkin-nonce-round-1"
    )
    account_after_round1 = await _get_account(account_id)
    assert account_after_round1.owner_instance is None
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(account_after_round1.access_token_encrypted) == "round1-access"

    # Round 2: distinct checkout + a distinct checkin nonce — proves the
    # second checkin genuinely re-imports rather than short-circuiting on a
    # stale settled nonce from round 1.
    await _round_trip(
        access_token="round2-access", refresh_token="round2-refresh", checkin_nonce="checkin-nonce-round-2"
    )
    account_after_round2 = await _get_account(account_id)
    assert account_after_round2.owner_instance is None
    assert encryptor.decrypt(account_after_round2.access_token_encrypted) == "round2-access"
    assert encryptor.decrypt(account_after_round2.refresh_token_encrypted) == "round2-refresh"


@pytest.mark.asyncio
async def test_execute_checkin_second_round_trip_mints_fresh_nonce(db_setup: bool) -> None:
    del db_setup
    account_id = "acc_execute_double_round_trip"
    checkin_nonces_seen: list[str] = []

    async def _observe(observed_account_id: str, nonce: str, auth: FederationAuthPayload) -> None:
        del observed_account_id, auth
        checkin_nonces_seen.append(nonce)

    async def _do_checkout(peer_nonce: str) -> None:
        peer = _FakePeerClient(checkout_result=_checkout_result(nonce=peer_nonce))
        async with SessionLocal() as session:
            service = FederationService(FederationRepository(session), settings=_settings(), peer_client=peer)
            await service.execute_checkout(account_id)

    async def _do_checkin() -> None:
        peer = _FakePeerClient(checkin_result=CheckinPeerResult(settled=True), checkin_observer=_observe)
        async with SessionLocal() as session:
            service = FederationService(FederationRepository(session), settings=_settings(), peer_client=peer)
            result = await service.execute_checkin(account_id)
            assert result.settled is True

    # Round 1: assume ownership, then check it back in.
    await _do_checkout("execute-checkout-nonce-round-1")
    await _do_checkin()

    # Round 2: assume ownership again (a fresh checkout nonce from the
    # peer), then check in again. Before the fix this reused round 1's
    # now-settled checkin nonce instead of minting a new one.
    await _do_checkout("execute-checkout-nonce-round-2")
    await _do_checkin()

    assert len(checkin_nonces_seen) == 2
    assert checkin_nonces_seen[0] != checkin_nonces_seen[1]
