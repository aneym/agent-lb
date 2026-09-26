from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select, update
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

    async def list_local_usage_rollups(self, *, window_days: int) -> list[FederationUsageDayRollup]:
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
                return account, pending.nonce
            if account.owner_instance not in (None, local_instance_id):
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
            transfer = await self.get_transfer_by_nonce(nonce)
            if transfer is not None:
                # Another request imported while we waited; never regress it.
                if account is None or account.owner_instance != local_instance_id:
                    raise ValueError("checkout nonce belongs to another owner")
                await self._session.rollback()
                return
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
            self._session.add(
                AccountTransfer(
                    id=str(uuid.uuid4()),
                    account_id=account_id,
                    nonce=nonce,
                    direction=AccountTransferDirection.CHECKOUT,
                    counterparty_instance_id=peer_id,
                    state=AccountTransferState.PENDING,
                )
            )
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
        if existing is not None and (existing.owner_instance is None or existing.owner_instance == local_instance_id):
            return False

        resolved_status = _coerce_account_status(status)
        if existing is not None:
            # Never overwrite a token in an unconfirmed checkout: its owner
            # must still be able to resend the original payload on retry.
            pending_checkout = (
                select(AccountTransfer.id)
                .where(
                    AccountTransfer.account_id == account_id,
                    AccountTransfer.direction == AccountTransferDirection.CHECKOUT,
                    AccountTransfer.state == AccountTransferState.PENDING,
                )
                .exists()
            )
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id, Account.owner_instance == existing.owner_instance)
                .where(Account.owner_instance.is_not(None), Account.owner_instance != local_instance_id)
                .values(
                    provider=provider,
                    email=email,
                    alias=alias,
                    status=resolved_status,
                    plan_type=plan_type,
                    chatgpt_account_id=chatgpt_account_id,
                    access_token_encrypted=encryptor.encrypt(access_token),
                    refresh_token_encrypted=case(
                        # When the checkout has not yet settled, this copy is
                        # the only payload its owner can resend. After the
                        # handshake, keep the inert placeholder on mirror
                        # cycles; the peer never exports its rotated token.
                        (pending_checkout, Account.refresh_token_encrypted),
                        else_=encryptor.encrypt(_MIRROR_REFRESH_TOKEN_PLACEHOLDER),
                    ),
                    owner_instance=owner_instance_id,
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
                    status=resolved_status,
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
            account = await self._session.get(Account, transfer.account_id, with_for_update=True)
            # Serialize with ownership changes before blanking the old copy.
            transfer = await self.get_transfer_by_nonce(nonce)
            if transfer is None or account is None or account.owner_instance != transfer.counterparty_instance_id:
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
        self, account_id: str, nonce: str, auth: FederationAuthPayload, *, encryptor: TokenEncryptor
    ) -> AccountTransfer:
        """Import the returned token and reopen the local gate in one commit."""
        try:
            account = await self._session.get(Account, account_id, with_for_update=True)
            if account is None:
                raise ValueError("account missing")
            transfer = await self.get_transfer_by_nonce(nonce)
            if transfer is not None:
                if transfer.account_id != account_id or transfer.direction != AccountTransferDirection.CHECKIN:
                    raise ValueError("nonce belongs to another transfer")
                if transfer.state == AccountTransferState.SETTLED:
                    return transfer
                if account.owner_instance != transfer.counterparty_instance_id:
                    raise ValueError("checkin owner does not match pending transfer")
            else:
                if account.owner_instance is None:
                    raise ValueError("account is already locally owned")
                transfer = AccountTransfer(
                    id=str(uuid.uuid4()),
                    account_id=account_id,
                    nonce=nonce,
                    direction=AccountTransferDirection.CHECKIN,
                    counterparty_instance_id=account.owner_instance,
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
