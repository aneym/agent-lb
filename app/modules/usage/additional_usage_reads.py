from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AdditionalUsageHistory
from app.modules.usage.additional_quota_keys import (
    AdditionalQuotaQueryScope,
    canonicalize_additional_quota_key,
    get_additional_quota_query_scope,
)


def _resolve_additional_quota_query_scope(
    *,
    quota_key: str | None = None,
    limit_name: str | None = None,
    metered_feature: str | None = None,
) -> AdditionalQuotaQueryScope | None:
    return get_additional_quota_query_scope(
        quota_key=quota_key,
        limit_name=limit_name,
        metered_feature=metered_feature,
    )


def _additional_quota_match_clause(scope: AdditionalQuotaQueryScope):
    clauses = [AdditionalUsageHistory.quota_key.in_(tuple(scope.quota_key_match_values or {scope.quota_key}))]
    if scope.limit_name_match_values:
        clauses.append(func.lower(AdditionalUsageHistory.limit_name).in_(tuple(scope.limit_name_match_values)))
    if scope.metered_feature_match_values:
        clauses.append(
            func.lower(AdditionalUsageHistory.metered_feature).in_(tuple(scope.metered_feature_match_values))
        )
    return or_(*clauses)


class AdditionalUsageReads:
    _session: AsyncSession

    async def latest_by_account(
        self,
        quota_key: str | None = None,
        window: str | None = None,
        *,
        limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:

        scope = _resolve_additional_quota_query_scope(
            quota_key=quota_key,
            limit_name=limit_name,
        )
        if scope is None or window is None:
            raise ValueError("quota_key/limit_name and window are required")
        conditions = [
            _additional_quota_match_clause(scope),
            AdditionalUsageHistory.window == window,
        ]
        if account_ids is not None:
            conditions.append(AdditionalUsageHistory.account_id.in_(account_ids))
        if since is not None:
            conditions.append(AdditionalUsageHistory.recorded_at >= since)
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name in {"sqlite", "postgresql"}:
            if account_ids is not None and not account_ids:
                return {}
            if account_ids is not None and len(account_ids) > 400:
                result: dict[str, AdditionalUsageHistory] = {}
                ids = sorted(set(account_ids))
                for offset in range(0, len(ids), 400):
                    result.update(
                        await self.latest_by_account(
                            quota_key,
                            window,
                            limit_name=limit_name,
                            account_ids=ids[offset : offset + 400],
                            since=since,
                        )
                    )
                return result
            accounts_stmt = (
                select(Account.id.label("account_id"))
                if bind.dialect.name == "postgresql"
                else select(AdditionalUsageHistory.account_id).distinct()
            )
            if account_ids is not None:
                accounts_stmt = union_all(
                    *(select(literal(account_id).label("account_id")) for account_id in sorted(set(account_ids)))
                )
            accounts = accounts_stmt.subquery("quota_accounts")
            matches = [
                AdditionalUsageHistory.quota_key == value
                for value in sorted(scope.quota_key_match_values or {scope.quota_key})
            ]
            matches.extend(
                func.lower(AdditionalUsageHistory.limit_name) == value
                for value in sorted(scope.limit_name_match_values)
            )
            matches.extend(
                func.lower(AdditionalUsageHistory.metered_feature) == value
                for value in sorted(scope.metered_feature_match_values)
            )
            order = (
                AdditionalUsageHistory.recorded_at.desc(),
                AdditionalUsageHistory.used_percent.desc(),
                AdditionalUsageHistory.id.desc(),
            )
            candidates = []
            for match in matches:
                seek_conditions = [
                    AdditionalUsageHistory.account_id == accounts.c.account_id,
                    AdditionalUsageHistory.window == window,
                    match,
                ]
                if since is not None:
                    seek_conditions.append(AdditionalUsageHistory.recorded_at >= since)
                candidates.append(
                    select(AdditionalUsageHistory.id)
                    .where(*seek_conditions)
                    .order_by(*order)
                    .limit(1)
                    .correlate(accounts)
                    .scalar_subquery()
                )
            latest_id = (
                select(AdditionalUsageHistory.id)
                .where(AdditionalUsageHistory.id.in_(candidates))
                .order_by(*order)
                .limit(1)
                .correlate(accounts)
                .scalar_subquery()
            )
            ids = select(latest_id.label("usage_id")).select_from(accounts).subquery("latest_quota_ids")
            stmt = select(AdditionalUsageHistory).join(ids, AdditionalUsageHistory.id == ids.c.usage_id)
        else:
            subq = (
                select(
                    AdditionalUsageHistory.id.label("usage_id"),
                    func.row_number()
                    .over(
                        partition_by=AdditionalUsageHistory.account_id,
                        order_by=(
                            AdditionalUsageHistory.recorded_at.desc(),
                            AdditionalUsageHistory.used_percent.desc(),
                            AdditionalUsageHistory.id.desc(),
                        ),
                    )
                    .label("row_number"),
                )
                .where(*conditions)
                .subquery()
            )
            stmt = (
                select(AdditionalUsageHistory)
                .join(subq, AdditionalUsageHistory.id == subq.c.usage_id)
                .where(subq.c.row_number == 1)
            )
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def latest_by_quota_key(
        self,
        quota_key: str,
        window: str,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        return await self.latest_by_account(
            quota_key=quota_key,
            window=window,
            account_ids=account_ids,
            since=since,
        )

    async def list_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        bind = self._session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            stmt = select(AdditionalUsageHistory.quota_key).distinct()
            if account_ids is not None:
                stmt = stmt.where(AdditionalUsageHistory.account_id.in_(account_ids))
            if since is not None:
                stmt = stmt.where(AdditionalUsageHistory.recorded_at >= since)
            result = await self._session.execute(stmt)
            return sorted(key for key in result.scalars().all() if key)

        stmt = select(
            AdditionalUsageHistory.quota_key,
            AdditionalUsageHistory.limit_name,
            AdditionalUsageHistory.metered_feature,
        ).distinct()
        if account_ids is not None:
            stmt = stmt.where(AdditionalUsageHistory.account_id.in_(account_ids))
        if since is not None:
            stmt = stmt.where(AdditionalUsageHistory.recorded_at >= since)
        result = await self._session.execute(stmt)
        resolved_keys = {
            resolved_key
            for quota_key_value, limit_name_value, metered_feature_value in result.all()
            if (
                resolved_key := canonicalize_additional_quota_key(
                    quota_key=quota_key_value,
                    limit_name=limit_name_value,
                    metered_feature=metered_feature_value,
                )
            )
            is not None
        }
        return sorted(resolved_keys)

    async def list_limit_names(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        return await self.list_quota_keys(account_ids=account_ids, since=since)

    async def history_since(
        self,
        account_id: str,
        quota_key: str | None = None,
        window: str | None = None,
        since: datetime | None = None,
        *,
        limit_name: str | None = None,
    ) -> list[AdditionalUsageHistory]:

        scope = _resolve_additional_quota_query_scope(
            quota_key=quota_key,
            limit_name=limit_name,
        )
        if scope is None or window is None or since is None:
            raise ValueError("account_id, quota_key/limit_name, window, and since are required")
        stmt = (
            select(AdditionalUsageHistory)
            .where(
                AdditionalUsageHistory.account_id == account_id,
                _additional_quota_match_clause(scope),
                AdditionalUsageHistory.window == window,
                AdditionalUsageHistory.recorded_at >= since,
            )
            .order_by(AdditionalUsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest_recorded_at_for_account(self, account_id: str) -> datetime | None:

        stmt = select(func.max(AdditionalUsageHistory.recorded_at)).where(
            AdditionalUsageHistory.account_id == account_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_recorded_at(self) -> datetime | None:

        stmt = select(func.max(AdditionalUsageHistory.recorded_at))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
