from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import String, and_, cast, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, ApiKey, RequestLog


@dataclass(frozen=True, slots=True)
class _RequestLogFilters:
    conditions: list
    needs_related_search_joins: bool


class RequestLogQueries:
    _session: AsyncSession

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
        include_total: bool = True,
    ) -> tuple[list[RequestLog], int]:
        filters = self._build_filters(
            search=search,
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=api_key_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=include_success,
            include_error_other=include_error_other,
            error_codes_in=error_codes_in,
            error_codes_excluding=error_codes_excluding,
            exclude_soft_deleted=True,
        )

        total_col = func.count().over().label("_total") if include_total else literal_column("0").label("_total")
        stmt = select(RequestLog, total_col).order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
        stmt = self._apply_related_search_joins(stmt, filters.needs_related_search_joins)
        if filters.conditions:
            stmt = stmt.where(and_(*filters.conditions))
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        rows = result.all()
        if not rows:
            return [], await self._count_recent(filters) if include_total else offset
        logs = [row[0] for row in rows]
        total = rows[0][1] if include_total else offset + len(logs)
        return logs, total

    async def _count_recent(self, filters: _RequestLogFilters) -> int:
        count_stmt = select(func.count(RequestLog.id)).select_from(RequestLog)
        count_stmt = self._apply_related_search_joins(count_stmt, filters.needs_related_search_joins)
        if filters.conditions:
            count_stmt = count_stmt.where(and_(*filters.conditions))
        result = await self._session.execute(count_stmt)
        return int(result.scalar_one())

    async def list_filter_options(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
    ) -> tuple[list[str], list[tuple[str, str | None]], list[str], list[tuple[str, str | None]]]:
        filters = self._build_filters(
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=api_key_ids,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
            exclude_soft_deleted=True,
        )
        api_key_facet_filters = self._build_filters(
            since=since,
            until=until,
            account_ids=account_ids,
            api_key_ids=None,
            model_options=model_options,
            models=models,
            reasoning_efforts=reasoning_efforts,
            include_success=True,
            include_error_other=True,
            error_codes_in=None,
            error_codes_excluding=None,
            exclude_soft_deleted=True,
        )

        account_stmt = select(RequestLog.account_id).distinct().order_by(RequestLog.account_id.asc())
        model_stmt = (
            select(RequestLog.model, RequestLog.reasoning_effort)
            .distinct()
            .order_by(RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        api_key_stmt = select(RequestLog.api_key_id).distinct().order_by(RequestLog.api_key_id.asc())
        status_stmt = (
            select(RequestLog.status, RequestLog.error_code)
            .distinct()
            .order_by(RequestLog.status.asc(), RequestLog.error_code.asc())
        )
        if filters.conditions:
            clause = and_(*filters.conditions)
            account_stmt = account_stmt.where(clause)
            model_stmt = model_stmt.where(clause)
            status_stmt = status_stmt.where(clause)
        if api_key_facet_filters.conditions:
            api_key_stmt = api_key_stmt.where(and_(*api_key_facet_filters.conditions))

        account_rows = await self._session.execute(account_stmt)
        model_rows = await self._session.execute(model_stmt)
        api_key_rows = await self._session.execute(api_key_stmt)
        status_rows = await self._session.execute(status_stmt)

        account_ids = [row[0] for row in account_rows.all() if row[0]]
        model_options = [(row[0], row[1]) for row in model_rows.all() if row[0]]
        api_key_ids = [row[0] for row in api_key_rows.all() if row[0]]
        status_values = [(row[0], row[1]) for row in status_rows.all() if row[0]]
        return account_ids, model_options, api_key_ids, status_values

    async def get_api_key_names_by_ids(self, api_key_ids: list[str]) -> dict[str, str]:
        unique_ids = sorted({key_id for key_id in api_key_ids if key_id})
        if not unique_ids:
            return {}
        result = await self._session.execute(select(ApiKey.id, ApiKey.name).where(ApiKey.id.in_(unique_ids)))
        return {key_id: name for key_id, name in result.all() if key_id and name}

    async def get_api_key_details_by_ids(self, api_key_ids: list[str]) -> dict[str, tuple[str, str | None]]:
        unique_ids = sorted({key_id for key_id in api_key_ids if key_id})
        if not unique_ids:
            return {}
        result = await self._session.execute(
            select(ApiKey.id, ApiKey.name, ApiKey.key_prefix).where(ApiKey.id.in_(unique_ids))
        )
        return {key_id: (name, key_prefix) for key_id, name, key_prefix in result.all() if key_id and name}

    def _build_filters(
        self,
        *,
        search: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        account_ids: list[str] | None = None,
        api_key_ids: list[str] | None = None,
        model_options: list[tuple[str, str | None]] | None = None,
        models: list[str] | None = None,
        reasoning_efforts: list[str] | None = None,
        include_success: bool = True,
        include_error_other: bool = True,
        error_codes_in: list[str] | None = None,
        error_codes_excluding: list[str] | None = None,
        exclude_soft_deleted: bool = False,
    ) -> _RequestLogFilters:
        conditions = []
        if exclude_soft_deleted:
            conditions.append(RequestLog.deleted_at.is_(None))
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at <= until)
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if api_key_ids:
            conditions.append(RequestLog.api_key_id.in_(api_key_ids))

        if model_options:
            pair_conditions = []
            for model, effort in model_options:
                base = (model or "").strip()
                if not base:
                    continue
                if effort is None:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort.is_(None)))
                else:
                    pair_conditions.append(and_(RequestLog.model == base, RequestLog.reasoning_effort == effort))
            if pair_conditions:
                conditions.append(or_(*pair_conditions))
        else:
            if models:
                conditions.append(RequestLog.model.in_(models))
            if reasoning_efforts:
                conditions.append(RequestLog.reasoning_effort.in_(reasoning_efforts))

        status_conditions = []
        if include_success:
            status_conditions.append(RequestLog.status == "success")
        if error_codes_in:
            status_conditions.append(and_(RequestLog.status == "error", RequestLog.error_code.in_(error_codes_in)))
        if include_error_other:
            error_clause = [RequestLog.status == "error"]
            if error_codes_excluding:
                error_clause.append(
                    or_(
                        RequestLog.error_code.is_(None),
                        ~RequestLog.error_code.in_(error_codes_excluding),
                    )
                )
            status_conditions.append(and_(*error_clause))
        if status_conditions:
            conditions.append(or_(*status_conditions))
        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    RequestLog.account_id.ilike(search_pattern),
                    Account.email.ilike(search_pattern),
                    RequestLog.request_id.ilike(search_pattern),
                    RequestLog.model.ilike(search_pattern),
                    RequestLog.reasoning_effort.ilike(search_pattern),
                    RequestLog.source.ilike(search_pattern),
                    RequestLog.status.ilike(search_pattern),
                    RequestLog.error_code.ilike(search_pattern),
                    RequestLog.error_message.ilike(search_pattern),
                    RequestLog.api_key_id.ilike(search_pattern),
                    ApiKey.name.ilike(search_pattern),
                    cast(RequestLog.requested_at, String).ilike(search_pattern),
                    cast(RequestLog.input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.output_tokens, String).ilike(search_pattern),
                    cast(RequestLog.cached_input_tokens, String).ilike(search_pattern),
                    cast(RequestLog.reasoning_tokens, String).ilike(search_pattern),
                    cast(RequestLog.latency_ms, String).ilike(search_pattern),
                )
            )
            return _RequestLogFilters(conditions=conditions, needs_related_search_joins=True)
        return _RequestLogFilters(conditions=conditions, needs_related_search_joins=False)

    def _apply_related_search_joins(self, stmt, include_related_search_joins: bool):
        if not include_related_search_joins:
            return stmt
        return stmt.outerjoin(Account, Account.id == RequestLog.account_id).outerjoin(
            ApiKey,
            ApiKey.id == RequestLog.api_key_id,
        )
