from __future__ import annotations

import uuid
from datetime import datetime

from cryptography.fernet import InvalidToken
from sqlalchemy import ColumnElement, delete, func, or_, select, update

from app.core.crypto import TokenEncryptor
from app.core.providers.gemini import GEMINI_PROVIDER_NAME
from app.core.utils.time import utcnow
from app.db.account_state_models import AccountExchangeIntent
from app.db.federation_models import AccountTransfer, AccountTransferDirection, AccountTransferState
from app.db.models import Account, AccountStatus
from app.modules.federation.owner_health import OWNER_STALE_AFTER
from app.modules.federation.repository_transfers import FederationTransferRepository
from app.modules.federation.repository_usage import FederationUsageRepository
from app.modules.federation.schemas import FederationAuthPayload

LOCAL_ONLY_PROVIDERS = frozenset({GEMINI_PROVIDER_NAME})


class FederationRepository(FederationTransferRepository, FederationUsageRepository):
    async def count_accounts_by_ownership(self, local_instance_id: str) -> tuple[int, int, int]:
        owned = or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id)
        stale = Account.access_expires_at < utcnow() - OWNER_STALE_AFTER
        counts = await self._session.execute(
            select(
                func.count().filter(owned), func.count().filter(~owned), func.count().filter(~owned, stale)
            ).select_from(Account)
        )
        return counts.one()._tuple()

    async def list_locally_owned_accounts(self, local_instance_id: str) -> list[Account]:
        stmt = (
            select(Account)
            .where(or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id))
            .where(Account.provider.notin_(LOCAL_ONLY_PROVIDERS))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_federation_accounts(self) -> list[Account]:
        return list(await self._session.scalars(select(Account)))

    async def release_for_checkout(
        self, account_id: str, taker_instance_id: str, *, local_instance_id: str, nonce: str
    ) -> AccountTransfer | None:

        try:
            locked = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
            if locked is None or await self.has_exchange_intent(account_id):
                await self._session.rollback()
                return None
            if (
                await self.get_open_transfer(account_id) is not None
                or await self.get_transfer_by_nonce(nonce) is not None
            ):
                await self._session.rollback()
                return None
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                .where(or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id))
                .values(owner_instance=taker_instance_id)
                .returning(Account.id)
            )
            if result.scalar_one_or_none() is None:
                await self._session.rollback()
                return None
            transfer = AccountTransfer(
                id=str(uuid.uuid4()),
                account_id=account_id,
                nonce=nonce,
                direction=AccountTransferDirection.CHECKOUT,
                counterparty_instance_id=taker_instance_id,
                state=AccountTransferState.PENDING,
            )
            self._session.add(transfer)
            await self._session.commit()
            return transfer
        except BaseException:
            await self._session.rollback()
            raise

    async def release_for_checkin(
        self, account_id: str, counterparty: str, *, local_instance_id: str, nonce: str
    ) -> tuple[Account | None, str]:

        try:
            account = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
            if account is None or await self.has_exchange_intent(account_id):
                await self._session.rollback()
                return None, nonce
            pending = await self.get_pending_transfer(
                account_id, direction=AccountTransferDirection.CHECKIN, counterparty_instance_id=counterparty
            )
            if pending and account.owner_instance == counterparty:
                pending_nonce = pending.nonce
                await self._session.commit()
                await self._session.refresh(account)
                return account, pending_nonce
            if (
                account.owner_instance not in (None, local_instance_id)
                or await self.get_open_transfer(account_id) is not None
            ):
                await self._session.rollback()
                return None, nonce
            account.owner_instance = counterparty
            self._session.add(
                AccountTransfer(
                    id=str(uuid.uuid4()),
                    account_id=account_id,
                    nonce=nonce,
                    direction=AccountTransferDirection.CHECKIN,
                    counterparty_instance_id=counterparty,
                    state=AccountTransferState.PENDING,
                )
            )
            await self._session.commit()
            await self._session.refresh(account)
            return account, nonce
        except BaseException:
            await self._session.rollback()
            raise

    async def import_checkout(
        self,
        account_id: str,
        nonce: str,
        peer_id: str,
        auth: FederationAuthPayload,
        *,
        local_instance_id: str,
        encryptor: TokenEncryptor,
    ) -> None:

        try:
            account = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)

            settled = await self._session.execute(
                update(AccountTransfer)
                .where(
                    AccountTransfer.nonce == nonce,
                    AccountTransfer.account_id == account_id,
                    AccountTransfer.direction == AccountTransferDirection.CHECKOUT,
                    AccountTransfer.counterparty_instance_id == peer_id,
                    AccountTransfer.state == AccountTransferState.PENDING,
                )
                .values(state=AccountTransferState.SETTLED, settled_at=utcnow())
                .returning(AccountTransfer.id)
            )
            if settled.scalar_one_or_none() is None:
                raise ValueError("checkout reservation is no longer pending")
            if await self.get_open_transfer(account_id) is not None:
                raise ValueError("account has another open transfer")
            if account is None:
                account = Account(
                    id=account_id,
                    provider=auth.provider,
                    email=auth.email,
                    alias=auth.alias,
                    status=_coerce_account_status(auth.status),
                    plan_type=auth.plan_type,
                    chatgpt_account_id=auth.chatgpt_account_id,
                    access_token_encrypted=encryptor.encrypt(auth.access_token),
                    refresh_token_encrypted=encryptor.encrypt(auth.refresh_token),
                    id_token_encrypted=encryptor.encrypt(auth.id_token) if auth.id_token else None,
                    last_refresh=utcnow(),
                    access_expires_at=_expiry_from_ms(auth.expires_at_ms),
                    owner_instance=local_instance_id,
                )
                self._session.add(account)
            else:
                if account.owner_instance in (None, local_instance_id):
                    raise ValueError("checkout would overwrite a locally owned token")
                account.provider = auth.provider
                account.email = auth.email
                account.alias = auth.alias
                account.status = _coerce_account_status(auth.status)
                account.plan_type = auth.plan_type
                account.chatgpt_account_id = auth.chatgpt_account_id
                account.access_token_encrypted = encryptor.encrypt(auth.access_token)
                account.refresh_token_encrypted = encryptor.encrypt(auth.refresh_token)
                account.id_token_encrypted = encryptor.encrypt(auth.id_token) if auth.id_token else None
                account.last_refresh = utcnow()
                account.access_expires_at = _expiry_from_ms(auth.expires_at_ms)
                account.owner_instance = local_instance_id
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    async def upsert_mirror_account(
        self,
        *,
        account_id: str,
        provider: str,
        email: str,
        alias: str | None,
        status: str,
        plan_type: str,
        chatgpt_account_id: str | None,
        access_token: str,
        owner_instance_id: str,
        local_instance_id: str,
        encryptor: TokenEncryptor,
        expires_at_ms: int | None = None,
        subscription_status: str | None = None,
        deactivation_reason: str | None = None,
    ) -> bool:
        subscription = {} if subscription_status is None else {"subscription_status": subscription_status}

        existing = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
        if existing is not None:
            if existing.owner_instance in (None, local_instance_id) or not _has_empty_refresh(existing, encryptor):
                await self._session.rollback()
                return False
            result = await self._session.execute(
                update(Account)
                .where(
                    Account.id == account_id,
                    Account.owner_instance == existing.owner_instance,
                    Account.refresh_token_encrypted == existing.refresh_token_encrypted,
                    *_without_custody_intents(open_transfers_only=existing.owner_instance == owner_instance_id),
                )
                .values(
                    provider=provider,
                    email=email,
                    alias=alias,
                    status=_coerce_account_status(status),
                    plan_type=plan_type,
                    chatgpt_account_id=chatgpt_account_id,
                    access_token_encrypted=encryptor.encrypt(access_token),
                    refresh_token_encrypted=encryptor.encrypt(""),
                    owner_instance=owner_instance_id,
                    deactivation_reason=deactivation_reason,
                    last_refresh=utcnow(),
                    access_expires_at=_expiry_from_ms(expires_at_ms),
                    **subscription,
                )
                .returning(Account.id)
            )
            if result.scalar_one_or_none() is None:
                await self._session.rollback()
                return False
        else:
            self._session.add(
                Account(
                    id=account_id,
                    provider=provider,
                    email=email,
                    alias=alias,
                    status=_coerce_account_status(status),
                    plan_type=plan_type,
                    chatgpt_account_id=chatgpt_account_id,
                    access_token_encrypted=encryptor.encrypt(access_token),
                    refresh_token_encrypted=encryptor.encrypt(""),
                    id_token_encrypted=None,
                    last_refresh=utcnow(),
                    access_expires_at=_expiry_from_ms(expires_at_ms),
                    owner_instance=owner_instance_id,
                    deactivation_reason=deactivation_reason,
                    **subscription,
                )
            )
        await self._session.commit()
        return True

    async def prune_mirror_accounts(
        self,
        *,
        owner_instance_id: str,
        local_instance_id: str,
        keep_ids: set[str],
        sync_started_at: datetime,
        encryptor: TokenEncryptor,
    ) -> bool:
        if owner_instance_id == local_instance_id:
            return False
        missing = (
            Account.owner_instance == owner_instance_id,
            Account.provider.notin_(LOCAL_ONLY_PROVIDERS),
            Account.last_refresh < sync_started_at,
            Account.id.not_in(keep_ids),
        )
        unleased = _without_custody_intents(open_transfers_only=False)
        candidates = await self._session.scalars(select(Account).where(*missing, *unleased).with_for_update())
        removed = False
        for account in candidates:
            if not _has_empty_refresh(account, encryptor):
                continue
            result = await self._session.execute(
                delete(Account)
                .where(*missing, *unleased, Account.id == account.id)
                .where(Account.refresh_token_encrypted == account.refresh_token_encrypted)
                .returning(Account.id)
            )
            removed = result.scalar_one_or_none() is not None or removed
        await self._session.commit()
        return removed

    async def has_owner_accounts(self, owner_instance_id: str) -> bool:
        return (
            await self._session.scalar(select(Account.id).where(Account.owner_instance == owner_instance_id).limit(1))
        ) is not None

    async def deactivate_mirrored_accounts_not_in(
        self, *, owner_instance_id: str, keep_ids: set[str], encryptor: TokenEncryptor | None = None
    ) -> list[str]:

        statement = select(Account.id).where(
            Account.owner_instance == owner_instance_id,
            or_(
                Account.status != AccountStatus.DEACTIVATED,
                Account.deactivation_reason != "federation_push_removed",
                Account.deactivation_reason.is_(None),
            ),
        )
        if keep_ids:
            statement = statement.where(Account.id.not_in(keep_ids))
        ids = list((await self._session.execute(statement)).scalars())
        if ids:
            await self._session.execute(
                update(Account)
                .where(Account.id.in_(ids), Account.owner_instance == owner_instance_id)
                .values(
                    status=AccountStatus.DEACTIVATED,
                    deactivation_reason="federation_push_removed",
                    access_token_encrypted=(encryptor or TokenEncryptor()).encrypt(""),
                )
            )
            await self._session.commit()
        return ids

    async def accept_checkin(
        self,
        account_id: str,
        nonce: str,
        caller_instance_id: str,
        auth: FederationAuthPayload,
        *,
        encryptor: TokenEncryptor,
    ) -> AccountTransfer:

        try:
            account = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
            if account is None:
                raise ValueError("account missing")
            if await self.has_exchange_intent(account_id):
                raise ValueError("account has an exchange in progress")
            transfer = await self.get_transfer_by_nonce(nonce)
            if transfer is not None:
                if transfer.account_id != account_id or transfer.direction != AccountTransferDirection.CHECKIN:
                    raise ValueError("nonce belongs to another transfer")
                if (
                    transfer.counterparty_instance_id != caller_instance_id
                    or transfer.state == AccountTransferState.ABORTED
                ):
                    raise ValueError("nonce cannot be imported")
                if transfer.state == AccountTransferState.SETTLED:
                    return transfer
                if account.owner_instance != transfer.counterparty_instance_id:
                    raise ValueError("checkin owner does not match pending transfer")
            else:
                if account.owner_instance != caller_instance_id:
                    raise ValueError("checkin owner differs from caller")
                if await self.get_open_transfer(account_id) is not None:
                    raise ValueError("account already has an open transfer")
                checkout = await self.get_latest_transfer(account_id, direction=AccountTransferDirection.CHECKOUT)
                if (
                    checkout is None
                    or checkout.counterparty_instance_id != caller_instance_id
                    or checkout.state != AccountTransferState.SETTLED
                ):
                    raise ValueError("no completed checkout to caller")
                transfer = AccountTransfer(
                    id=str(uuid.uuid4()),
                    account_id=account_id,
                    nonce=nonce,
                    direction=AccountTransferDirection.CHECKIN,
                    counterparty_instance_id=caller_instance_id,
                    state=AccountTransferState.PENDING,
                )
                self._session.add(transfer)
            account.access_token_encrypted = encryptor.encrypt(auth.access_token)
            account.refresh_token_encrypted = encryptor.encrypt(auth.refresh_token)
            account.id_token_encrypted = encryptor.encrypt(auth.id_token) if auth.id_token else None
            account.last_refresh = utcnow()
            account.access_expires_at = _expiry_from_ms(auth.expires_at_ms)
            account.owner_instance = None
            transfer.state = AccountTransferState.SETTLED
            transfer.settled_at = utcnow()
            await self._session.commit()
            return transfer
        except BaseException:
            await self._session.rollback()
            raise


def _coerce_account_status(value: str) -> AccountStatus:
    try:
        return AccountStatus(value)
    except ValueError:
        return AccountStatus.ACTIVE


def _has_empty_refresh(account: Account, encryptor: TokenEncryptor) -> bool:
    try:
        return encryptor.decrypt(account.refresh_token_encrypted) == ""
    except (InvalidToken, UnicodeError):
        return False


def _without_custody_intents(*, open_transfers_only: bool) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    transfers = select(AccountTransfer.id).where(AccountTransfer.account_id == Account.id)
    if open_transfers_only:
        transfers = transfers.where(
            AccountTransfer.state.in_((AccountTransferState.PENDING, AccountTransferState.ABORTING))
        )
    exchanges = select(AccountExchangeIntent.account_id).where(AccountExchangeIntent.account_id == Account.id)
    return ~transfers.exists(), ~exchanges.exists()


def _expiry_from_ms(expires_at_ms: int | None) -> datetime | None:
    return datetime.utcfromtimestamp(expires_at_ms / 1000) if expires_at_ms is not None else None
