from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountStatus
from app.db.settings_models import DashboardSettings
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
        values: dict[str, object | None] = {
            "status": status,
            "deactivation_reason": deactivation_reason,
            "reset_at": reset_at,
        }
        if blocked_at is not _UNSET:
            values["blocked_at"] = blocked_at
        stmt = update(Account).where(Account.id == account_id)
        if expected_refresh_token_encrypted is not None:
            stmt = stmt.where(Account.refresh_token_encrypted == expected_refresh_token_encrypted)
        elif status != AccountStatus.EXCHANGE_UNCERTAIN:
            stmt = stmt.where(Account.status != AccountStatus.EXCHANGE_UNCERTAIN)
        result = await self._session.execute(stmt.values(**values).returning(Account.id))
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_security_work_authorized(self, account_id: str, enabled: bool) -> bool:
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
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
    ) -> bool:
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
            .where(Account.status == expected_status)
            .values(**values)
            .returning(Account.id)
        )
        if expected_status == AccountStatus.EXCHANGE_UNCERTAIN and status != expected_status:
            return False
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
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_alias(self, account_id: str, alias: str | None) -> bool:
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(alias=alias).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_limit_warmup_enabled(self, account_id: str, enabled: bool) -> bool:
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(limit_warmup_enabled=enabled).returning(Account.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def update_routing_policy(self, account_id: str, routing_policy: str) -> bool:
        result = await self._session.execute(
            update(Account).where(Account.id == account_id).values(routing_policy=routing_policy).returning(Account.id)
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
