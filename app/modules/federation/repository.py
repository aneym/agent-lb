from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AccountTransfer,
    AccountTransferDirection,
    AccountTransferState,
    FederationUsageDaily,
    RequestLog,
)
from app.modules.federation.schemas import FederationUsageDayRollup

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
                "last_request_at": rollup.last_request_at,
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
            await self._session.execute(
                select(FederationUsageDaily).where(FederationUsageDaily.day >= earliest_day)
            )
        ).scalars().all()
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
            select(func.count()).select_from(Account).where(
                or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id)
            )
        )
        mirrored = await self._session.scalar(
            select(func.count()).select_from(Account).where(
                and_(Account.owner_instance.is_not(None), Account.owner_instance != local_instance_id)
            )
        )
        return int(owned or 0), int(mirrored or 0)

    async def list_locally_owned_accounts(self, local_instance_id: str) -> list[Account]:
        stmt = select(Account).where(or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def set_owner_instance(self, account_id: str, owner_instance: str | None) -> None:
        await self._session.execute(
            update(Account).where(Account.id == account_id).values(owner_instance=owner_instance)
        )
        await self._session.commit()

    async def set_owner_instance_if_locally_owned(
        self, account_id: str, new_owner: str, *, local_instance_id: str
    ) -> bool:
        """Atomic compare-and-set for the checkout release step.

        A plain read-check-then-write would leave a window where two
        concurrent checkouts (by different takers) both observe "locally
        owned" and both write, producing a double owner. This folds the
        check into the UPDATE's WHERE clause so only one concurrent caller
        can ever match and win.
        """
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .where(or_(Account.owner_instance.is_(None), Account.owner_instance == local_instance_id))
            .values(owner_instance=new_owner)
            .returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def import_tokens(
        self,
        account_id: str,
        *,
        encryptor: TokenEncryptor,
        access_token: str,
        refresh_token: str,
        id_token: str | None,
        last_refresh: datetime,
    ) -> None:
        await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                access_token_encrypted=encryptor.encrypt(access_token),
                refresh_token_encrypted=encryptor.encrypt(refresh_token),
                id_token_encrypted=encryptor.encrypt(id_token) if id_token else None,
                last_refresh=last_refresh,
            )
        )
        await self._session.commit()

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
    ) -> bool:
        """Create-or-update a mirrored row. Returns False (no-op) when the row
        is locally owned — a checkout must never be clobbered by a stale
        mirror cycle."""
        existing = await self._session.get(Account, account_id)
        if existing is not None and (existing.owner_instance is None or existing.owner_instance == local_instance_id):
            return False

        resolved_status = _coerce_account_status(status)
        if existing is not None:
            existing.provider = provider
            existing.email = email
            existing.alias = alias
            existing.status = resolved_status
            existing.plan_type = plan_type
            existing.chatgpt_account_id = chatgpt_account_id
            existing.access_token_encrypted = encryptor.encrypt(access_token)
            existing.owner_instance = owner_instance_id
            existing.last_refresh = utcnow()
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
