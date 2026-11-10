from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Executable

from app.core.utils.time import to_utc_naive
from app.db.models import Account, StickySessionKind
from app.db.session import sqlite_writer_section
from app.db.session_models import StickySession
from app.modules.federation.sticky_pin_schemas import StickyPin

SYNCED_KINDS = (StickySessionKind.CODEX_SESSION, StickySessionKind.STICKY_THREAD)


class StickyPinStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._postgres = session.get_bind().dialect.name == "postgresql"

    def _bound_at(self) -> ColumnElement[datetime]:
        if self._postgres:
            return func.timezone("UTC", cast(StickySession.updated_at, DateTime(timezone=True)), type_=DateTime())
        return StickySession.updated_at.expression

    def _as_stored(self, value: datetime) -> ColumnElement[datetime]:
        bound = literal(to_utc_naive(value), DateTime())
        if self._postgres:
            return cast(func.timezone("UTC", bound), DateTime())
        return bound

    async def list_pins(self, *, since: datetime | None, after_key: str | None, limit: int) -> list[StickyPin]:
        statement = select(
            StickySession.key,
            StickySession.kind,
            StickySession.account_id,
            self._bound_at(),
        ).where(StickySession.kind.in_(SYNCED_KINDS))
        if since is not None:
            statement = statement.where(StickySession.updated_at > self._as_stored(since))
        if after_key is not None:
            statement = statement.where(StickySession.key > after_key)
        statement = statement.order_by(StickySession.key, StickySession.kind).limit(limit)
        rows = (await self._session.execute(statement)).all()
        return [
            StickyPin(key=key, kind=kind.value, account_id=account_id, bound_at=bound_at)
            for key, kind, account_id, bound_at in rows
        ]

    async def apply(self, pins: list[StickyPin], *, seed: bool) -> int:
        if not pins:
            return 0
        known_accounts = set(
            (
                await self._session.execute(select(Account.id).where(Account.id.in_({pin.account_id for pin in pins})))
            ).scalars()
        )
        current = {
            (key, kind.value): (account_id, bound_at)
            for key, kind, account_id, bound_at in (
                await self._session.execute(
                    select(
                        StickySession.key,
                        StickySession.kind,
                        StickySession.account_id,
                        self._bound_at(),
                    ).where(
                        StickySession.key.in_({pin.key for pin in pins}),
                        StickySession.kind.in_(SYNCED_KINDS),
                    )
                )
            ).all()
        }
        statements: list[Executable] = []
        for pin in pins:
            if pin.account_id not in known_accounts:
                continue
            bound_at = to_utc_naive(pin.bound_at)
            seen = current.get((pin.key, pin.kind))
            if seen is None:
                statements.append(self._insert(pin, bound_at))
            elif seen[0] != pin.account_id and (seed or bound_at > seen[1]):
                statements.append(self._rebind(pin, bound_at, seen_account_id=seen[0], seed=seed))
        if not statements:
            return 0
        async with sqlite_writer_section(self._session):
            for statement in statements:
                await self._session.execute(statement)
            await self._session.commit()
        return len(statements)

    def _insert(self, pin: StickyPin, bound_at: datetime) -> Executable:
        insert_fn = pg_insert if self._postgres else sqlite_insert
        stored = self._as_stored(bound_at)
        return (
            insert_fn(StickySession)
            .values(
                key=pin.key,
                kind=StickySessionKind(pin.kind),
                account_id=pin.account_id,
                created_at=stored,
                updated_at=stored,
            )
            .on_conflict_do_nothing(index_elements=[StickySession.key, StickySession.kind])
        )

    def _rebind(self, pin: StickyPin, bound_at: datetime, *, seen_account_id: str, seed: bool) -> Executable:
        statement = update(StickySession).where(
            StickySession.key == pin.key,
            StickySession.kind == StickySessionKind(pin.kind),
            StickySession.account_id == seen_account_id,
        )
        if not seed:
            statement = statement.where(StickySession.updated_at < self._as_stored(bound_at))
        return statement.values(account_id=pin.account_id, updated_at=self._as_stored(bound_at))
