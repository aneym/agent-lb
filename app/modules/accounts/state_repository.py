from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountStatus, DashboardSettings
from app.db.session import sqlite_writer_section
from app.db.session_models import StickySession
from app.modules.usage.additional_quota_keys import normalize_additional_quota_routing_policy_overrides

_SETTINGS_ROW_ID = 1
_UNSET = object()


class AccountStateRepository:
    _session: AsyncSession

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        async with sqlite_writer_section(self._session):
            previous_status = await self._session.scalar(
                select(Account.status).where(Account.id == account_id).with_for_update()
            )
            values: dict[str, object | None] = {
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
            }
            if blocked_at is not _UNSET:
                values["blocked_at"] = blocked_at
            stmt = update(Account).where(Account.id == account_id).where(Account.delete_requested_at.is_(None))
            if expected_refresh_token_encrypted is not None:
                stmt = stmt.where(Account.refresh_token_encrypted == expected_refresh_token_encrypted)
            elif status != AccountStatus.EXCHANGE_UNCERTAIN:
                stmt = stmt.where(Account.status != AccountStatus.EXCHANGE_UNCERTAIN)
            result = await self._session.execute(stmt.values(**values).returning(Account.id))
            updated_id = result.scalar_one_or_none()
            if updated_id is not None:
                await self._finish_status_transition(account_id, previous_status, status)
            await self._session.commit()
            return updated_id is not None

    async def update_security_work_authorized(self, account_id: str, enabled: bool) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                .where(Account.delete_requested_at.is_(None))
                .values(security_work_authorized=enabled)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None | object = _UNSET,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool:
        async with sqlite_writer_section(self._session):
            values: dict[str, object | None] = {
                "status": status,
                "deactivation_reason": deactivation_reason,
                "reset_at": reset_at,
            }
            if blocked_at is not _UNSET:
                values["blocked_at"] = blocked_at
            stmt = (
                update(Account)
                .where(Account.id == account_id)
                .where(Account.delete_requested_at.is_(None))
                .where(Account.status == expected_status)
                .values(**values)
                .returning(Account.id)
            )
            if (
                expected_status == AccountStatus.EXCHANGE_UNCERTAIN
                and status != expected_status
                and expected_refresh_token_encrypted is None
            ):
                return False
            if expected_refresh_token_encrypted is not None:
                stmt = stmt.where(Account.refresh_token_encrypted == expected_refresh_token_encrypted)
            if expected_deactivation_reason is None:
                stmt = stmt.where(Account.deactivation_reason.is_(None))
            else:
                stmt = stmt.where(Account.deactivation_reason == expected_deactivation_reason)
            if expected_reset_at is None:
                stmt = stmt.where(Account.reset_at.is_(None))
            else:
                stmt = stmt.where(Account.reset_at == expected_reset_at)
            if expected_blocked_at is not _UNSET:
                if expected_blocked_at is None:
                    stmt = stmt.where(Account.blocked_at.is_(None))
                else:
                    stmt = stmt.where(Account.blocked_at == expected_blocked_at)
            result = await self._session.execute(stmt)
            updated_id = result.scalar_one_or_none()
            if updated_id is not None:
                await self._finish_status_transition(account_id, expected_status, status)
            await self._session.commit()
            return updated_id is not None

    async def _finish_status_transition(
        self, account_id: str, previous_status: AccountStatus | None, status: AccountStatus
    ) -> None:
        if self._hard_sticky_outage_started(previous_status, status):
            await self._refresh_hard_sticky_outage_grace(account_id)
        if status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
            await self._close_http_bridge_sessions_for_account(account_id)

    async def update_alias(self, account_id: str, alias: str | None) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                .where(Account.delete_requested_at.is_(None))
                .values(alias=alias)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_limit_warmup_enabled(self, account_id: str, enabled: bool) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                .where(Account.delete_requested_at.is_(None))
                .values(limit_warmup_enabled=enabled)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_routing_policy(self, account_id: str, routing_policy: str) -> bool:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(Account)
                .where(Account.id == account_id)
                .where(Account.delete_requested_at.is_(None))
                .values(routing_policy=routing_policy)
                .returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_subscription_ledger(
        self,
        account_id: str,
        *,
        status: str | None,
        next_charge_at: datetime | None,
        current_period_end_at: datetime | None,
        amount: float | None,
        currency: str | None,
        last_verified_at: datetime | None,
        notes: str | None,
    ) -> bool:
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                subscription_status=status,
                subscription_next_charge_at=next_charge_at,
                subscription_current_period_end_at=current_period_end_at,
                subscription_amount=amount,
                subscription_currency=currency,
                subscription_last_verified_at=last_verified_at,
                subscription_notes=notes,
            )
            .returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def additional_quota_routing_policy_overrides(self) -> dict[str, str]:
        settings = await self._session.get(DashboardSettings, _SETTINGS_ROW_ID)
        if settings is None or not settings.additional_quota_routing_policies_json:
            return {}
        try:
            parsed = json.loads(settings.additional_quota_routing_policies_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        string_policies = {
            key: value for key, value in parsed.items() if isinstance(key, str) and isinstance(value, str)
        }
        return normalize_additional_quota_routing_policy_overrides(string_policies)
