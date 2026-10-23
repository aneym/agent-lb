from __future__ import annotations

from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.auth import extract_id_token_claims, resolve_seat_identity
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AccountUsageRollup,
    ApiKeyAccountAssignment,
    HttpBridgeSessionAlias,
    HttpBridgeSessionRecord,
    HttpBridgeSessionState,
    RuntimeSentinel,
    StickySessionKind,
    UsageHistory,
)
from app.db.request_models import RequestLog
from app.db.session import sqlite_writer_section
from app.db.session_models import StickySession
from app.modules.accounts.repository_state import (
    _HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL,
    _HARD_STICKY_UNAVAILABLE_STATUSES,
    ACCOUNT_PENDING_DELETION_REASON,
    _clear_request_usage_summary_cache,
    credentials_replaced_since_wipe,
)
from app.modules.accounts.usage_rollup import lock_fold_state
from app.modules.accounts.usage_time_rollup import (
    mirror_account_hard_delete_into_time_rollups,
    mirror_account_soft_delete_into_time_rollups,
)
from app.modules.usage.repository import _clear_bulk_history_since_sqlite_cache


class AccountDeleteRepository:
    @staticmethod
    def _hard_sticky_outage_started(previous_status: AccountStatus | None, status: AccountStatus) -> bool:
        return (
            previous_status is not None
            and previous_status not in _HARD_STICKY_UNAVAILABLE_STATUSES
            and (status in _HARD_STICKY_UNAVAILABLE_STATUSES)
        )

    async def _refresh_hard_sticky_outage_grace(self, account_id: str) -> None:
        await self._session.execute(
            update(StickySession)
            .where(StickySession.account_id == account_id, StickySession.kind == StickySessionKind.CODEX_SESSION)
            .values(updated_at=utcnow())
        )

    async def seed_hard_sticky_outage_grace_on_startup(self) -> int:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"Hard-sticky outage grace seeding sentinel unsupported for dialect={dialect!r}")
        stamp_stmt = (
            insert_fn(RuntimeSentinel)
            .values(name=_HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL, value=utcnow().isoformat())
            .on_conflict_do_nothing(index_elements=[RuntimeSentinel.name])
            .returning(RuntimeSentinel.name)
        )
        async with sqlite_writer_section():
            stamp_result = await self._session.execute(stamp_stmt)
            stamped_by_this_boot = stamp_result.scalar_one_or_none() is not None
            if not stamped_by_this_boot:
                await self._session.commit()
                return 0
            account_ids = (
                await self._session.scalars(
                    select(Account.id).where(Account.status.in_(_HARD_STICKY_UNAVAILABLE_STATUSES))
                )
            ).all()
            for account_id in account_ids:
                await self._refresh_hard_sticky_outage_grace(account_id)
            await self._session.commit()
        return len(account_ids)

    async def _close_http_bridge_sessions_for_account(self, account_id: str) -> None:
        session_ids = select(HttpBridgeSessionRecord.id).where(HttpBridgeSessionRecord.account_id == account_id)
        await self._session.execute(
            delete(HttpBridgeSessionAlias).where(HttpBridgeSessionAlias.session_id.in_(session_ids))
        )
        await self._session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.account_id == account_id)
            .values(
                account_id=None,
                state=HttpBridgeSessionState.CLOSED,
                closed_at=utcnow(),
                owner_instance_id=None,
                lease_expires_at=None,
                latest_turn_state=None,
                latest_response_id=None,
                latest_input_item_count=None,
                latest_input_full_fingerprint=None,
                latest_pending_tool_calls_json=None,
            )
        )

    async def begin_delete(self, account_id: str, *, delete_history: bool = False) -> bool:
        marked_row = (
            await self._session.execute(
                select(
                    Account.delete_requested_at,
                    Account.access_token_encrypted,
                    Account.refresh_token_encrypted,
                    Account.id_token_encrypted,
                ).where(Account.id == account_id)
            )
        ).first()
        if (
            marked_row is not None
            and marked_row[0] is not None
            and (not credentials_replaced_since_wipe(marked_row[1], marked_row[2], marked_row[3]))
        ):
            return True
        encryptor = TokenEncryptor()
        wiped_token = encryptor.encrypt("")
        async with sqlite_writer_section():
            seat_stmt = select(Account.chatgpt_user_id, Account.id_token_encrypted).where(Account.id == account_id)
            if self._dialect_name() == "postgresql":
                seat_stmt = seat_stmt.with_for_update(key_share=True)
            seat_row = (await self._session.execute(seat_stmt)).first()
            if seat_row is None:
                await self._session.rollback()
                return False
            seat_user_id: str | None = seat_row[0]
            if seat_user_id is None:
                try:
                    claims = extract_id_token_claims(encryptor.decrypt(seat_row[1]))
                    seat_user_id = resolve_seat_identity(claims, claims.auth)
                except Exception:
                    seat_user_id = None
            values: dict[str, Any] = {
                "status": AccountStatus.DEACTIVATED,
                "deactivation_reason": ACCOUNT_PENDING_DELETION_REASON,
                "reset_at": None,
                "blocked_at": None,
                "access_token_encrypted": wiped_token,
                "refresh_token_encrypted": wiped_token,
                "id_token_encrypted": wiped_token,
                "delete_requested_at": func.coalesce(Account.delete_requested_at, utcnow()),
                "delete_history_requested": case(
                    (Account.delete_requested_at.is_(None), delete_history), else_=Account.delete_history_requested
                ),
            }
            if seat_user_id is not None:
                values["chatgpt_user_id"] = seat_user_id
            result = await self._session.execute(
                update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
            )
            updated_id = result.scalar_one_or_none()
            if updated_id is not None:
                await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
                await self._close_http_bridge_sessions_for_account(account_id)
                await self._session.execute(
                    delete(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.account_id == account_id)
                )
            await self._session.commit()
            return updated_id is not None

    async def delete(self, account_id: str, *, delete_history: bool = False, only_pending: bool = False) -> bool:
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                locked_account = await self._lock_postgresql_account_identity_membership(account_id, None)
                pending_state = (
                    None
                    if locked_account is None
                    else (
                        locked_account.delete_requested_at,
                        locked_account.delete_history_requested,
                        locked_account.access_token_encrypted,
                        locked_account.refresh_token_encrypted,
                        locked_account.id_token_encrypted,
                    )
                )
            else:
                pending_state = (
                    await self._session.execute(
                        select(
                            Account.delete_requested_at,
                            Account.delete_history_requested,
                            Account.access_token_encrypted,
                            Account.refresh_token_encrypted,
                            Account.id_token_encrypted,
                        ).where(Account.id == account_id)
                    )
                ).first()
            if only_pending:
                if pending_state is None or pending_state[0] is None:
                    await self._session.rollback()
                    return False
                if credentials_replaced_since_wipe(pending_state[2], pending_state[3], pending_state[4]):
                    await self._session.execute(
                        update(Account)
                        .where(Account.id == account_id)
                        .values(delete_requested_at=None, delete_history_requested=False)
                    )
                    await self._session.commit()
                    return False
                delete_history = bool(pending_state[1])
            await lock_fold_state(self._session)
            if self._dialect_name() == "postgresql":
                await self._session.execute(select(Account.id).where(Account.id == account_id).with_for_update())
            await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
            if delete_history:
                await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
                await mirror_account_hard_delete_into_time_rollups(self._session, account_id)
            else:
                await self._session.execute(
                    update(RequestLog)
                    .where(RequestLog.account_id == account_id)
                    .values(account_id=None, deleted_at=utcnow())
                )
                await mirror_account_soft_delete_into_time_rollups(self._session, account_id)
            await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
            await self._session.execute(delete(AccountUsageRollup).where(AccountUsageRollup.account_id == account_id))
            result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
            deleted_id = result.scalar_one_or_none()
            await self._session.commit()
            if deleted_id is not None:
                _clear_bulk_history_since_sqlite_cache()
                _clear_request_usage_summary_cache()
            return deleted_id is not None
