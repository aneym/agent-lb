from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import (
    Account,
    AccountExchangeIntent,
    AccountStatus,
    AccountTransfer,
    AccountTransferDirection,
    AccountTransferState,
    FederationUsageDaily,
    RequestLog,
)
from app.modules.federation.schemas import FederationAuthPayload, FederationUsageDayRollup

# Mirrored-only rows never carry a real refresh token (the owner never exports
# one over /mirror), but the column is NOT NULL. This placeholder is inert:
# the ownership gate in AuthManager guarantees a non-owned row is never
# selected for refresh, so it is never decrypted for an OAuth call.
_MIRROR_REFRESH_TOKEN_PLACEHOLDER = ""


@dataclass(frozen=True, slots=True)
class StoredFederationUsageRollup:
    instance_id: str
    rollup: FederationUsageDayRollup
    reported_at: datetime


class FederationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def reload_account(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id, populate_existing=True)

    async def has_exchange_intent(self, account_id: str) -> bool:
        return (await self._session.get(AccountExchangeIntent, account_id)) is not None

    async def upsert_usage_report(
        self,
        instance_id: str,
        rollups: list[FederationUsageDayRollup],
        *,
        reported_at: datetime,
    ) -> None:
        for rollup in rollups:
            key = {
                "instance_id": instance_id,
                "account_id": rollup.account_id,
                "day": rollup.day,
            }
            values = {
                "provider": rollup.provider,
                "requests": rollup.requests,
                "input_tokens": rollup.input_tokens,
                "output_tokens": rollup.output_tokens,
                "cache_read_tokens": rollup.cache_read_tokens,
                "cost": rollup.cost,
                "session_count": rollup.session_count,
                "last_request_at": to_utc_naive(rollup.last_request_at) if rollup.last_request_at else None,
                "reported_at": reported_at,
            }
            primary_key = tuple(key.values())
            existing = await self._session.get(FederationUsageDaily, primary_key)
            if existing is None:
                try:
                    async with self._session.begin_nested():
                        self._session.add(FederationUsageDaily(**key, **values))
                        await self._session.flush()
                except IntegrityError:
                    existing = await self._session.get(FederationUsageDaily, primary_key)
                    if existing is None:
                        raise
                else:
                    continue
            for field_name, value in values.items():
                setattr(existing, field_name, value)
        await self._session.commit()

    async def list_local_usage_rollups(
        self, *, window_days: int, account_ids: set[str] | None = None
    ) -> list[FederationUsageDayRollup]:
        earliest_day = (utcnow() - timedelta(days=window_days)).date()
        since = datetime.combine(earliest_day, datetime.min.time())
        day = func.date(RequestLog.requested_at)
        statement = (
            select(
                day.label("day"),
                RequestLog.account_id,
                func.max(RequestLog.provider).label("provider"),
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cache_read_tokens), 0).label("cache_read_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost"),
                func.count(func.distinct(RequestLog.session_id)).label("session_count"),
                func.max(RequestLog.requested_at).label("last_request_at"),
            )
            .where(
                and_(
                    RequestLog.account_id.is_not(None),
                    RequestLog.deleted_at.is_(None),
                    RequestLog.requested_at >= since,
                )
            )
            .group_by(day, RequestLog.account_id)
            .order_by(day.desc(), RequestLog.account_id.asc())
        )
        if account_ids is not None:
            statement = statement.where(RequestLog.account_id.in_(account_ids))
        rows = (await self._session.execute(statement)).all()
        return [
            FederationUsageDayRollup(
                day=date.fromisoformat(str(row.day)),
                account_id=str(row.account_id),
                provider=str(row.provider),
                requests=int(row.requests),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cache_read_tokens=int(row.cache_read_tokens),
                cost=float(row.cost),
                session_count=int(row.session_count),
                last_request_at=row.last_request_at,
            )
            for row in rows
        ]

    async def list_stored_usage_rollups(self, *, window_days: int) -> list[StoredFederationUsageRollup]:
        earliest_day = (utcnow() - timedelta(days=window_days)).date()
        rows = (
            (await self._session.execute(select(FederationUsageDaily).where(FederationUsageDaily.day >= earliest_day)))
            .scalars()
            .all()
        )
        return [
            StoredFederationUsageRollup(
                instance_id=row.instance_id,
                rollup=FederationUsageDayRollup(
                    day=row.day,
                    account_id=row.account_id,
                    provider=row.provider,
                    requests=row.requests,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    cache_read_tokens=row.cache_read_tokens,
                    cost=row.cost,
                    session_count=row.session_count,
                    last_request_at=row.last_request_at,
                ),
                reported_at=row.reported_at,
            )
            for row in rows
        ]

    async def count_accounts_by_ownership(self, local_instance_id: str) -> tuple[int, int]:
        owned = await self._session.scalar(
            select(func.count())
            .select_from(Account)
            .where(or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id))
        )
        mirrored = await self._session.scalar(
            select(func.count())
            .select_from(Account)
            .where(and_(Account.owner_instance.is_not(None), Account.owner_instance != local_instance_id))
        )
        return int(owned or 0), int(mirrored or 0)

    async def list_locally_owned_accounts(self, local_instance_id: str) -> list[Account]:
        stmt = select(Account).where(or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def release_for_checkout(
        self, account_id: str, taker_instance_id: str, *, local_instance_id: str, nonce: str
    ) -> AccountTransfer | None:
        """Publish the owner gate and retryable transfer together, or neither."""
        try:
            # Both the intent insert and this handoff lock the account row.
            # Recheck the fence *after* acquiring that lock (including after a
            # lost advisory lock), not in an earlier request-scoped snapshot.
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
        """Fence refresh and persist a retryable return in one transaction."""
        try:
            account = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
            if account is None or await self.has_exchange_intent(account_id):
                await self._session.rollback()
                return None, nonce
            pending = await self.get_pending_transfer(
                account_id, direction=AccountTransferDirection.CHECKIN, counterparty_instance_id=counterparty
            )
            if pending and account.owner_instance == counterparty:
                # No transaction or row lock may remain open across the peer call.
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
        """Import the live token, local owner gate and retry nonce atomically."""
        try:
            account = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
            # The caller's committed reservation is the serialization point with
            # reclaim. A tombstone or an aborting reservation cannot import.
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
    ) -> bool:
        """Create-or-update a mirrored row. Returns False (no-op) when the row
        is locally owned — a checkout must never be clobbered by a stale
        mirror cycle."""
        existing = await self._session.get(Account, account_id)
        if existing is not None:
            pending = (
                select(AccountTransfer.id)
                .where(
                    AccountTransfer.account_id == account_id,
                    AccountTransfer.state.in_((AccountTransferState.PENDING, AccountTransferState.ABORTING)),
                )
                .exists()
            )
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id, Account.owner_instance == existing.owner_instance)
                .where(Account.owner_instance.is_not(None), Account.owner_instance != local_instance_id)
                .where(~pending)
                .values(
                    provider=provider,
                    email=email,
                    alias=alias,
                    status=_coerce_account_status(status),
                    plan_type=plan_type,
                    chatgpt_account_id=chatgpt_account_id,
                    access_token_encrypted=encryptor.encrypt(access_token),
                    refresh_token_encrypted=encryptor.encrypt(_MIRROR_REFRESH_TOKEN_PLACEHOLDER),
                    owner_instance=owner_instance_id,
                    deactivation_reason=None,
                    last_refresh=utcnow(),
                    access_expires_at=_expiry_from_ms(expires_at_ms),
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
                    refresh_token_encrypted=encryptor.encrypt(_MIRROR_REFRESH_TOKEN_PLACEHOLDER),
                    id_token_encrypted=None,
                    last_refresh=utcnow(),
                    access_expires_at=_expiry_from_ms(expires_at_ms),
                    owner_instance=owner_instance_id,
                )
            )
        await self._session.commit()
        return True

    async def has_owner_accounts(self, owner_instance_id: str) -> bool:
        return (await self._session.scalar(
            select(Account.id).where(Account.owner_instance == owner_instance_id).limit(1)
        )) is not None

    async def deactivate_mirrored_accounts_not_in(
        self, *, owner_instance_id: str, keep_ids: set[str], encryptor: TokenEncryptor | None = None
    ) -> list[str]:
        """Blank dropped access credentials without changing any other owner's rows."""
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

    async def get_open_transfer(self, account_id: str) -> AccountTransfer | None:
        return (
            await self._session.execute(
                select(AccountTransfer)
                .where(
                    AccountTransfer.account_id == account_id,
                    AccountTransfer.state.in_((AccountTransferState.PENDING, AccountTransferState.ABORTING)),
                )
                .order_by(AccountTransfer.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def reserve_checkout(self, account_id: str, counterparty: str, nonce: str) -> AccountTransfer:
        try:
            account = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
            if account is None or account.owner_instance != counterparty:
                raise ValueError("checkout owner changed")
            if await self.get_open_transfer(account_id) is not None:
                raise ValueError("account already has an open transfer")
            transfer = AccountTransfer(
                id=str(uuid.uuid4()),
                account_id=account_id,
                nonce=nonce,
                direction=AccountTransferDirection.CHECKOUT,
                counterparty_instance_id=counterparty,
                state=AccountTransferState.PENDING,
            )
            self._session.add(transfer)
            await self._session.commit()
            return transfer
        except BaseException:
            await self._session.rollback()
            raise

    async def begin_checkout_abort(self, nonce: str) -> AccountTransfer:
        try:
            transfer = (
                await self._session.execute(
                    select(AccountTransfer)
                    .where(AccountTransfer.nonce == nonce)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            if transfer.direction != AccountTransferDirection.CHECKOUT:
                raise ValueError("checkout reservation cannot be aborted")
            if transfer.state == AccountTransferState.SETTLED:
                self._session.expunge(transfer)
                await self._session.rollback()
                return transfer
            if transfer.state not in (AccountTransferState.PENDING, AccountTransferState.ABORTING):
                raise ValueError("checkout reservation cannot be aborted")
            transfer.state = AccountTransferState.ABORTING
            await self._session.commit()
            return transfer
        except BaseException:
            await self._session.rollback()
            raise

    async def finish_abort(self, nonce: str, *, checkin: bool = False, local_instance_id: str = "") -> None:
        try:
            transfer = (
                await self._session.execute(
                    select(AccountTransfer)
                    .where(AccountTransfer.nonce == nonce)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            if transfer.state not in (AccountTransferState.PENDING, AccountTransferState.ABORTING):
                raise ValueError("transfer no longer open")
            if checkin:
                account = await self._session.get(
                    Account, transfer.account_id, with_for_update=True, populate_existing=True
                )
                if account is None or account.owner_instance != transfer.counterparty_instance_id:
                    raise ValueError("checkin owner changed")
                account.owner_instance = local_instance_id
            transfer.state = AccountTransferState.ABORTED
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    async def settle_checkin_and_blank(self, nonce: str, *, encryptor: TokenEncryptor) -> None:
        try:
            transfer = (
                await self._session.execute(
                    select(AccountTransfer)
                    .where(AccountTransfer.nonce == nonce)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            if transfer.direction != AccountTransferDirection.CHECKIN or transfer.state not in (
                AccountTransferState.PENDING,
                AccountTransferState.SETTLED,
            ):
                raise ValueError("checkin not pending or settled")
            account = await self._session.get(
                Account, transfer.account_id, with_for_update=True, populate_existing=True
            )
            if account is None or account.owner_instance != transfer.counterparty_instance_id:
                raise ValueError("checkin owner changed")
            account.refresh_token_encrypted = encryptor.encrypt(_MIRROR_REFRESH_TOKEN_PLACEHOLDER)
            transfer.state = AccountTransferState.SETTLED
            transfer.settled_at = utcnow()
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    async def abort_peer_transfer(
        self,
        nonce: str,
        account_id: str,
        direction: AccountTransferDirection,
        caller_instance_id: str,
        *,
        local_instance_id: str,
    ) -> AccountTransferState:
        """A durable abort answer; a missing nonce leaves a tombstone."""
        try:
            account = await self._session.get(Account, account_id, with_for_update=True, populate_existing=True)
            if account is None:
                raise ValueError("account missing")
            transfer = (
                await self._session.execute(
                    select(AccountTransfer)
                    .where(AccountTransfer.nonce == nonce)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if transfer is None:
                transfer = AccountTransfer(
                    id=str(uuid.uuid4()),
                    account_id=account_id,
                    nonce=nonce,
                    direction=direction,
                    counterparty_instance_id=caller_instance_id,
                    state=AccountTransferState.ABORTED,
                )
                self._session.add(transfer)
                await self._session.commit()
                return AccountTransferState.ABORTED
            if (
                transfer.account_id != account_id
                or transfer.direction != direction
                or transfer.counterparty_instance_id != caller_instance_id
            ):
                raise ValueError("abort does not match transfer")
            if transfer.state == AccountTransferState.SETTLED:
                await self._session.rollback()
                return AccountTransferState.SETTLED
            if transfer.state != AccountTransferState.ABORTED:
                if direction == AccountTransferDirection.CHECKOUT:
                    if account.owner_instance != caller_instance_id:
                        raise ValueError("checkout owner changed")
                    account.owner_instance = local_instance_id
                elif account.owner_instance != caller_instance_id:
                    raise ValueError("checkin owner changed")
                transfer.state = AccountTransferState.ABORTED
                await self._session.commit()
            return AccountTransferState.ABORTED
        except BaseException:
            await self._session.rollback()
            raise

    async def get_pending_transfer(
        self,
        account_id: str,
        *,
        direction: AccountTransferDirection,
        counterparty_instance_id: str,
    ) -> AccountTransfer | None:
        stmt = (
            select(AccountTransfer)
            .where(
                AccountTransfer.account_id == account_id,
                AccountTransfer.direction == direction,
                AccountTransfer.counterparty_instance_id == counterparty_instance_id,
                AccountTransfer.state == AccountTransferState.PENDING,
            )
            .order_by(AccountTransfer.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_transfer(
        self,
        account_id: str,
        *,
        direction: AccountTransferDirection,
    ) -> AccountTransfer | None:
        stmt = (
            select(AccountTransfer)
            .where(AccountTransfer.account_id == account_id, AccountTransfer.direction == direction)
            .order_by(AccountTransfer.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_transfer_by_nonce(self, nonce: str) -> AccountTransfer | None:
        result = await self._session.execute(select(AccountTransfer).where(AccountTransfer.nonce == nonce))
        return result.scalar_one_or_none()

    async def create_transfer(
        self,
        *,
        account_id: str,
        direction: AccountTransferDirection,
        counterparty_instance_id: str,
        nonce: str,
    ) -> AccountTransfer:
        transfer = AccountTransfer(
            id=str(uuid.uuid4()),
            account_id=account_id,
            nonce=nonce,
            direction=direction,
            counterparty_instance_id=counterparty_instance_id,
            state=AccountTransferState.PENDING,
        )
        self._session.add(transfer)
        await self._session.commit()
        await self._session.refresh(transfer)
        return transfer

    async def settle_checkout_and_blank_refresh(
        self, nonce: str, *, encryptor: TokenEncryptor
    ) -> AccountTransfer | None:
        """Only erase the exported refresh token after the taker confirms import."""
        transfer = await self.get_transfer_by_nonce(nonce)
        if transfer is None or transfer.direction != AccountTransferDirection.CHECKOUT:
            return None
        try:
            account = await self._session.get(
                Account, transfer.account_id, with_for_update=True, populate_existing=True
            )
            # Serialize with ownership changes before blanking the old copy.
            transfer = await self._session.get(
                AccountTransfer, transfer.id, with_for_update=True, populate_existing=True
            )
            if (
                transfer is None
                or account is None
                or account.owner_instance != transfer.counterparty_instance_id
                or transfer.state == AccountTransferState.ABORTED
            ):
                await self._session.rollback()
                return None
            if transfer.state != AccountTransferState.SETTLED:
                account.refresh_token_encrypted = encryptor.encrypt(_MIRROR_REFRESH_TOKEN_PLACEHOLDER)
                transfer.state = AccountTransferState.SETTLED
                transfer.settled_at = utcnow()
                await self._session.commit()
            return transfer
        except BaseException:
            await self._session.rollback()
            raise

    async def accept_checkin(
        self,
        account_id: str,
        nonce: str,
        caller_instance_id: str,
        auth: FederationAuthPayload,
        *,
        encryptor: TokenEncryptor,
    ) -> AccountTransfer:
        """Import the returned token and reopen the local gate in one commit."""
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

    async def mark_transfer_settled(self, nonce: str) -> AccountTransfer | None:
        transfer = await self.get_transfer_by_nonce(nonce)
        if transfer is None:
            return None
        if transfer.state != AccountTransferState.SETTLED:
            transfer.state = AccountTransferState.SETTLED
            transfer.settled_at = utcnow()
            await self._session.commit()
            await self._session.refresh(transfer)
        return transfer


def _coerce_account_status(value: str) -> AccountStatus:
    try:
        return AccountStatus(value)
    except ValueError:
        return AccountStatus.ACTIVE


def _expiry_from_ms(expires_at_ms: int | None) -> datetime | None:
    return datetime.utcfromtimestamp(expires_at_ms / 1000) if expires_at_ms is not None else None
