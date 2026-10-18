from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountExchangeIntent,
    AccountTransfer,
    AccountTransferDirection,
    AccountTransferState,
)


class FederationTransferRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def reload_account(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id, populate_existing=True)

    async def has_exchange_intent(self, account_id: str) -> bool:
        return (await self._session.get(AccountExchangeIntent, account_id)) is not None

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
            account.refresh_token_encrypted = encryptor.encrypt("")
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

        transfer = await self.get_transfer_by_nonce(nonce)
        if transfer is None or transfer.direction != AccountTransferDirection.CHECKOUT:
            return None
        try:
            account = await self._session.get(
                Account, transfer.account_id, with_for_update=True, populate_existing=True
            )

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
                account.refresh_token_encrypted = encryptor.encrypt("")
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
