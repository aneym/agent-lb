from __future__ import annotations

import logging
import secrets
from datetime import datetime

from app.core.auth import token_expiry_epoch_ms
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountTransferDirection
from app.modules.accounts.auth_manager import _cross_process_refresh_lock
from app.modules.federation.exceptions import (
    FederationConflictError,
    FederationNotConfiguredError,
    FederationNotFoundError,
)
from app.modules.federation.peer_client import AiohttpFederationPeerClient, FederationPeerClient
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import (
    FederationAuthPayload,
    FederationCheckinExecuteResponse,
    FederationCheckoutExecuteResponse,
    FederationCheckoutResponse,
    FederationMirrorAccount,
    FederationMirrorResponse,
    FederationTransferStatusResponse,
    FederationUsageAccount,
    FederationUsageDay,
    FederationUsageDayRollup,
    FederationUsageInstance,
    FederationUsageInstancesResponse,
    FederationUsageReportResponse,
    FederationUsageTotals,
)

logger = logging.getLogger(__name__)


class FederationService:
    def __init__(
        self,
        repository: FederationRepository,
        *,
        settings: Settings | None = None,
        encryptor: TokenEncryptor | None = None,
        peer_client: FederationPeerClient | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings or get_settings()
        self._encryptor = encryptor or TokenEncryptor()
        self._peer_client = peer_client or AiohttpFederationPeerClient()

    # --- owner-side: exports current state / receives transfer requests ---

    async def accept_usage_report(
        self, instance_id: str, rollups: list[FederationUsageDayRollup]
    ) -> FederationUsageReportResponse:
        reported_at = utcnow()
        await self._repo.upsert_usage_report(instance_id, rollups, reported_at=reported_at)
        return FederationUsageReportResponse(
            instance_id=instance_id,
            accepted=len(rollups),
            reported_at=reported_at,
        )

    async def get_usage_instances(self) -> FederationUsageInstancesResponse:
        local_id = self._settings.local_instance_id
        window_days = self._settings.federation_usage_window_days
        local_rollups = await self._repo.list_local_usage_rollups(window_days=window_days)
        stored = await self._repo.list_stored_usage_rollups(window_days=window_days)
        by_instance: dict[str, list[tuple[FederationUsageDayRollup, datetime | None]]] = {
            local_id: [(rollup, None) for rollup in local_rollups]
        }
        for stored_rollup in stored:
            if stored_rollup.instance_id == local_id:
                continue
            by_instance.setdefault(stored_rollup.instance_id, []).append(
                (stored_rollup.rollup, stored_rollup.reported_at)
            )
        return FederationUsageInstancesResponse(
            window_days=window_days,
            instances=[
                self._build_usage_instance(instance_id, rows) for instance_id, rows in sorted(by_instance.items())
            ],
        )

    async def build_mirror_response(self) -> FederationMirrorResponse:
        local_id = self._settings.local_instance_id
        accounts = await self._repo.list_locally_owned_accounts(local_id)
        mirror_accounts = []
        for account in accounts:
            access_token = self._encryptor.decrypt(account.access_token_encrypted)
            mirror_accounts.append(
                FederationMirrorAccount(
                    account_id=account.id,
                    provider=account.provider,
                    alias=account.alias,
                    email=account.email,
                    status=account.status.value,
                    plan_type=account.plan_type,
                    chatgpt_account_id=account.chatgpt_account_id,
                    access_token=access_token,
                    expires_at_ms=_account_expiry_ms(account, access_token),
                )
            )
        return FederationMirrorResponse(instance_id=local_id, accounts=mirror_accounts)

    async def checkout(self, account_id: str, taker_instance_id: str) -> FederationCheckoutResponse:
        async with _cross_process_refresh_lock(account_id):
            return await self._checkout_locked(account_id, taker_instance_id)

    async def _checkout_locked(self, account_id: str, taker_instance_id: str) -> FederationCheckoutResponse:
        local_id = self._settings.local_instance_id
        # Discard the request-scoped identity map after waiting for a refresh.
        account = await self._repo.reload_account(account_id)
        if account is None:
            raise FederationNotFoundError(account_id)
        if await self._repo.has_exchange_intent(account_id):
            raise FederationConflictError(account_id, current_owner=account.owner_instance)

        # Guarded UPDATE, not read-then-write: the ownership check is folded
        # into the UPDATE's WHERE clause so two concurrent checkouts by
        # different takers cannot both observe "locally owned" and both win
        # (that would double-own the account). Owner's gate closes the
        # instant this commits, before the payload is read back and
        # returned — design.md ordering.
        transfer = await self._repo.release_for_checkout(
            account_id, taker_instance_id, local_instance_id=local_id, nonce=secrets.token_urlsafe(32)
        )
        if transfer is not None:
            account = await self._repo.get_account(account_id)
            assert account is not None
            return FederationCheckoutResponse(
                account_id=account_id,
                nonce=transfer.nonce,
                owner_instance_id=local_id,
                auth=self._auth_payload(account),
            )

        # Either the account was not locally owned to begin with, or this
        # call lost a concurrent race to another checkout. Re-read: an
        # idempotent retry by the SAME taker that already won succeeds,
        # everything else is a conflict.
        account = await self._repo.get_account(account_id)
        if account is None:
            raise FederationNotFoundError(account_id)
        if account.owner_instance == taker_instance_id:
            pending = await self._repo.get_pending_transfer(
                account_id,
                direction=AccountTransferDirection.CHECKOUT,
                counterparty_instance_id=taker_instance_id,
            )
            if pending is not None:
                # A retry may happen after the taker rotated its token. Never
                # import the original payload over a locally owned live copy.
                return FederationCheckoutResponse(
                    account_id=account_id,
                    nonce=pending.nonce,
                    owner_instance_id=local_id,
                    auth=self._auth_payload(account),
                )

        raise FederationConflictError(account_id, current_owner=account.owner_instance)

    async def confirm_checkout(self, nonce: str) -> FederationTransferStatusResponse:
        transfer = await self._repo.settle_checkout_and_blank_refresh(nonce, encryptor=self._encryptor)
        if transfer is None:
            raise FederationNotFoundError(nonce)
        return FederationTransferStatusResponse(account_id=transfer.account_id, nonce=nonce, state=transfer.state.value)

    async def checkin(
        self, account_id: str, nonce: str, auth: FederationAuthPayload
    ) -> FederationTransferStatusResponse:
        account = await self._repo.get_account(account_id)
        if account is None:
            raise FederationNotFoundError(account_id)
        try:
            settled = await self._repo.accept_checkin(account_id, nonce, auth, encryptor=self._encryptor)
        except ValueError as exc:
            raise FederationConflictError(account_id, current_owner=account.owner_instance) from exc
        return FederationTransferStatusResponse(account_id=account_id, nonce=nonce, state=settled.state.value)

    async def reclaim(self, account_id: str) -> bool:
        # A pending transfer does not prove the peer never imported the token.
        # Without peer acknowledgement, reopening ownership double-spends it.
        return False

    # --- taker-side: operator-triggered, calls the configured peer ---

    async def execute_checkout(self, account_id: str) -> FederationCheckoutExecuteResponse:
        local_id = self._settings.local_instance_id
        peer_url, token = self._require_peer_config()

        result = await self._peer_client.checkout(
            peer_url=peer_url,
            token=token,
            account_id=account_id,
            taker_instance_id=local_id,
        )
        # Durably import + assume BEFORE confirming with the peer: safe
        # because the peer's gate already closed when it returned this
        # payload (design.md). A confirm failure below must not undo this.
        transfer = await self._repo.get_transfer_by_nonce(result.nonce)
        if transfer is None:
            await self._repo.import_checkout(
                account_id,
                result.nonce,
                result.owner_instance_id,
                result.auth,
                local_instance_id=local_id,
                encryptor=self._encryptor,
            )
        else:
            existing = await self._repo.reload_account(account_id)
            if existing is None or existing.owner_instance != local_id:
                raise FederationConflictError(account_id, current_owner=existing.owner_instance if existing else None)
            # Already imported this nonce. Do not replace a rotated token with
            # the peer's stale snapshot (or the peer's mirror placeholder).

        confirmed = False
        try:
            await self._peer_client.checkout_confirm(peer_url=peer_url, token=token, nonce=result.nonce)
        except Exception:
            logger.warning(
                "Federation checkout confirm failed account_id=%s nonce=%s; account remains locally owned, "
                "transfer left unconfirmed for retry",
                account_id,
                result.nonce,
                exc_info=True,
            )
        else:
            await self._repo.mark_transfer_settled(result.nonce)
            confirmed = True

        return FederationCheckoutExecuteResponse(
            account_id=account_id,
            nonce=result.nonce,
            owner_instance=local_id,
            confirmed=confirmed,
        )

    async def execute_checkin(self, account_id: str) -> FederationCheckinExecuteResponse:
        peer_url, token = self._require_peer_config()
        async with _cross_process_refresh_lock(account_id):
            counterparty = await self._resolve_checkin_target(account_id)
            if counterparty is None:
                raise FederationConflictError(account_id, current_owner=None)
            account, nonce = await self._repo.release_for_checkin(
                account_id,
                counterparty,
                local_instance_id=self._settings.local_instance_id,
                nonce=secrets.token_urlsafe(32),
            )
            if account is None:
                raise FederationConflictError(account_id, current_owner=None)
            auth = self._auth_payload(account)

        result = await self._peer_client.checkin(
            peer_url=peer_url,
            token=token,
            account_id=account_id,
            nonce=nonce,
            auth=auth,
        )
        await self._repo.mark_transfer_settled(nonce)
        return FederationCheckinExecuteResponse(account_id=account_id, nonce=nonce, settled=result.settled)

    # --- internal helpers ---

    @staticmethod
    def _totals(rollups: list[FederationUsageDayRollup]) -> FederationUsageTotals:
        return FederationUsageTotals(
            requests=sum(row.requests for row in rollups),
            input_tokens=sum(row.input_tokens for row in rollups),
            output_tokens=sum(row.output_tokens for row in rollups),
            cost=sum(row.cost for row in rollups),
        )

    @classmethod
    def _build_usage_instance(
        cls,
        instance_id: str,
        rows: list[tuple[FederationUsageDayRollup, datetime | None]],
    ) -> FederationUsageInstance:
        days: list[FederationUsageDay] = []
        for day in sorted({rollup.day for rollup, _ in rows}, reverse=True):
            day_rows = [(rollup, reported_at) for rollup, reported_at in rows if rollup.day == day]
            days.append(
                FederationUsageDay(
                    day=day,
                    totals=cls._totals([rollup for rollup, _ in day_rows]),
                    accounts=[
                        FederationUsageAccount(
                            **rollup.model_dump(),
                            reported_at=reported_at,
                        )
                        for rollup, reported_at in day_rows
                    ],
                )
            )
        return FederationUsageInstance(
            instance_id=instance_id,
            totals=cls._totals([rollup for rollup, _ in rows]),
            days=days,
        )

    def _auth_payload(self, account: Account) -> FederationAuthPayload:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        id_token = self._encryptor.decrypt(account.id_token_encrypted) if account.id_token_encrypted else None
        return FederationAuthPayload(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            expires_at_ms=_account_expiry_ms(account, access_token),
            provider=account.provider,
            email=account.email,
            alias=account.alias,
            status=account.status.value,
            plan_type=account.plan_type,
            chatgpt_account_id=account.chatgpt_account_id,
        )

    async def _resolve_checkin_target(self, account_id: str) -> str | None:
        transfer = await self._repo.get_latest_transfer(account_id, direction=AccountTransferDirection.CHECKOUT)
        if transfer is not None:
            return transfer.counterparty_instance_id
        return None

    def _require_peer_config(self) -> tuple[str, str]:
        peer_url = self._settings.federation_peer_url
        token = self._settings.federation_transfer_token
        if not peer_url or not token:
            raise FederationNotConfiguredError()
        return peer_url, token


def _account_expiry_ms(account: Account, token: str) -> int | None:
    if account.access_expires_at is not None:
        return naive_utc_to_epoch(account.access_expires_at) * 1000
    return token_expiry_epoch_ms(token)


def _expiry_from_ms(expires_at_ms: int | None) -> datetime | None:
    return datetime.utcfromtimestamp(expires_at_ms / 1000) if expires_at_ms is not None else None
