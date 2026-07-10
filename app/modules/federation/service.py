from __future__ import annotations

import logging
import secrets

from app.core.auth import token_expiry_epoch_ms
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountTransferDirection
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
                    expires_at_ms=token_expiry_epoch_ms(access_token),
                )
            )
        return FederationMirrorResponse(instance_id=local_id, accounts=mirror_accounts)

    async def checkout(self, account_id: str, taker_instance_id: str) -> FederationCheckoutResponse:
        local_id = self._settings.local_instance_id
        account = await self._repo.get_account(account_id)
        if account is None:
            raise FederationNotFoundError(account_id)

        # Guarded UPDATE, not read-then-write: the ownership check is folded
        # into the UPDATE's WHERE clause so two concurrent checkouts by
        # different takers cannot both observe "locally owned" and both win
        # (that would double-own the account). Owner's gate closes the
        # instant this commits, before the payload is read back and
        # returned — design.md ordering.
        won_race = await self._repo.set_owner_instance_if_locally_owned(
            account_id, taker_instance_id, local_instance_id=local_id
        )
        if won_race:
            transfer = await self._repo.create_transfer(
                account_id=account_id,
                direction=AccountTransferDirection.CHECKOUT,
                counterparty_instance_id=taker_instance_id,
                nonce=secrets.token_urlsafe(32),
            )
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
                return FederationCheckoutResponse(
                    account_id=account_id,
                    nonce=pending.nonce,
                    owner_instance_id=local_id,
                    auth=self._auth_payload(account),
                )

        raise FederationConflictError(account_id, current_owner=account.owner_instance)

    async def confirm_checkout(self, nonce: str) -> FederationTransferStatusResponse:
        transfer = await self._repo.mark_transfer_settled(nonce)
        if transfer is None:
            raise FederationNotFoundError(nonce)
        return FederationTransferStatusResponse(account_id=transfer.account_id, nonce=nonce, state=transfer.state.value)

    async def checkin(
        self, account_id: str, nonce: str, auth: FederationAuthPayload
    ) -> FederationTransferStatusResponse:
        existing_transfer = await self._repo.get_transfer_by_nonce(nonce)
        if existing_transfer is not None and existing_transfer.state.value == "settled":
            # Idempotent retry after success: never re-import a (possibly
            # stale) payload once the owner has already accepted one.
            return FederationTransferStatusResponse(
                account_id=existing_transfer.account_id, nonce=nonce, state="settled"
            )

        account = await self._repo.get_account(account_id)
        if account is None:
            raise FederationNotFoundError(account_id)

        if existing_transfer is None:
            # account.owner_instance is still the returning instance (T) at
            # this point — the real counterparty, not a guess. The "unknown"
            # fallback only fires in the degenerate case where this instance
            # already considers the account locally owned (e.g. a checkin
            # replayed after it was already processed some other way); the
            # field is bookkeeping/visibility only and never gates a
            # decision, so a placeholder there is harmless.
            await self._repo.create_transfer(
                account_id=account_id,
                direction=AccountTransferDirection.CHECKIN,
                counterparty_instance_id=account.owner_instance or "unknown",
                nonce=nonce,
            )

        await self._repo.import_tokens(
            account_id,
            encryptor=self._encryptor,
            access_token=auth.access_token,
            refresh_token=auth.refresh_token,
            id_token=auth.id_token,
            last_refresh=utcnow(),
        )
        await self._repo.set_owner_instance(account_id, None)
        settled = await self._repo.mark_transfer_settled(nonce)
        assert settled is not None
        return FederationTransferStatusResponse(account_id=account_id, nonce=nonce, state=settled.state.value)

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
        await self._import_auth_payload(account_id, result.auth, owner_instance=local_id)

        transfer = await self._repo.get_transfer_by_nonce(result.nonce)
        if transfer is None:
            transfer = await self._repo.create_transfer(
                account_id=account_id,
                direction=AccountTransferDirection.CHECKOUT,
                counterparty_instance_id=result.owner_instance_id,
                nonce=result.nonce,
            )

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
            nonce=transfer.nonce,
            owner_instance=local_id,
            confirmed=confirmed,
        )

    async def execute_checkin(self, account_id: str) -> FederationCheckinExecuteResponse:
        peer_url, token = self._require_peer_config()
        counterparty = await self._resolve_checkin_target(account_id)
        if counterparty is None:
            raise FederationConflictError(account_id, current_owner=None)

        # Local gate closes FIRST — nobody refreshes from this instant
        # (design.md). Never unilaterally reclaimed after this point.
        await self._repo.set_owner_instance(account_id, counterparty)

        account = await self._repo.get_account(account_id)
        if account is None:
            raise FederationNotFoundError(account_id)
        auth = self._auth_payload(account)

        # Reuse a nonce only while a checkin for this counterparty is still
        # PENDING (a genuine lost-response retry). A SETTLED transfer from a
        # prior round trip must never be reused: the owner's nonce
        # idempotency would answer "settled" without re-importing, silently
        # dropping this round's (possibly rotated) tokens while the local
        # gate has already closed — stranding the account unowned nowhere.
        pending_transfer = await self._repo.get_pending_transfer(
            account_id,
            direction=AccountTransferDirection.CHECKIN,
            counterparty_instance_id=counterparty,
        )
        if pending_transfer is not None:
            nonce = pending_transfer.nonce
        else:
            nonce = secrets.token_urlsafe(32)
            await self._repo.create_transfer(
                account_id=account_id,
                direction=AccountTransferDirection.CHECKIN,
                counterparty_instance_id=counterparty,
                nonce=nonce,
            )

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

    def _auth_payload(self, account: Account) -> FederationAuthPayload:
        access_token = self._encryptor.decrypt(account.access_token_encrypted)
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        id_token = self._encryptor.decrypt(account.id_token_encrypted) if account.id_token_encrypted else None
        return FederationAuthPayload(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            expires_at_ms=token_expiry_epoch_ms(access_token),
            provider=account.provider,
            email=account.email,
            alias=account.alias,
            status=account.status.value,
            plan_type=account.plan_type,
            chatgpt_account_id=account.chatgpt_account_id,
        )

    async def _import_auth_payload(self, account_id: str, auth: FederationAuthPayload, *, owner_instance: str) -> None:
        existing = await self._repo.get_account(account_id)
        if existing is None:
            await self._repo.upsert_mirror_account(
                account_id=account_id,
                provider=auth.provider,
                email=auth.email,
                alias=auth.alias,
                status=auth.status,
                plan_type=auth.plan_type,
                chatgpt_account_id=auth.chatgpt_account_id,
                access_token=auth.access_token,
                owner_instance_id=owner_instance,
                local_instance_id=self._settings.local_instance_id,
                encryptor=self._encryptor,
            )
        await self._repo.import_tokens(
            account_id,
            encryptor=self._encryptor,
            access_token=auth.access_token,
            refresh_token=auth.refresh_token,
            id_token=auth.id_token,
            last_refresh=utcnow(),
        )
        await self._repo.set_owner_instance(account_id, owner_instance)

    async def _resolve_checkin_target(self, account_id: str) -> str | None:
        transfer = await self._repo.get_latest_transfer(account_id, direction=AccountTransferDirection.CHECKOUT)
        if transfer is not None:
            return transfer.counterparty_instance_id
        return None

    def _require_peer_config(self) -> tuple[str, str]:
        peer_url = self._settings.federation_peer_url
        token = self._settings.federation_token
        if not peer_url or not token:
            raise FederationNotConfiguredError()
        return peer_url, token
