from __future__ import annotations

import json
from datetime import datetime
from typing import cast as typing_cast

import anyio
from sqlalchemy import and_, select
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.clients.failure_observation import failure_fields
from app.core.context_parts.capture import measured_parts
from app.core.eval_guard import eval_guard_state
from app.core.identity import RequestIdentity, identity_for_log
from app.core.model_aliases import requested_model_alias
from app.core.usage.logs import RequestLogLike, calculated_cost_from_log
from app.core.utils.client_session import (  # noqa: I001  # fmt: skip
    get_caller_seat,
    get_client_session_id,
    get_dispatch_id,
    get_job_id,
    get_lane,
    get_room,
)
from app.core.utils.request_id import ensure_request_id
from app.core.utils.time import utcnow
from app.db.models import Account
from app.db.request_models import RequestKind, RequestLog
from app.db.session import sqlite_writer_section
from app.modules.request_logs.aggregates import RequestLogAggregates
from app.modules.request_logs.edge_source import exclude_forwarded_edge_logs
from app.modules.request_logs.queries import RequestLogQueries
from app.modules.session_trace.recorder import store_context_parts


class RequestLogsRepository(RequestLogAggregates, RequestLogQueries):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_since(self, since: datetime) -> list[RequestLog]:
        result = await self._session.execute(
            select(RequestLog).where(
                RequestLog.requested_at >= since,
                self._exclude_warmup_clause(),
            )
        )
        return list(result.scalars().all())

    async def find_latest_account_id_for_response_id(
        self,
        *,
        response_id: str,
        api_key_id: str | None,
        session_id: str | None = None,
    ) -> str | None:
        response_id_value = response_id.strip()
        if not response_id_value:
            return None

        base_conditions = [
            RequestLog.request_id == response_id_value,
            RequestLog.status == "success",
            RequestLog.account_id.is_not(None),
            exclude_forwarded_edge_logs(),
        ]
        if api_key_id is not None:
            base_conditions.append(RequestLog.api_key_id == api_key_id)

        async def _lookup_account_id(conditions: list[ColumnElement[bool]]) -> str | None:
            stmt = (
                select(RequestLog.account_id)
                .where(and_(*conditions))
                .order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
                .limit(1)
            )
            result = await self._session.execute(stmt)
            account_id = result.scalar_one_or_none()
            if not isinstance(account_id, str):
                return None
            stripped = account_id.strip()
            return stripped or None

        session_id_value = session_id.strip() if isinstance(session_id, str) else ""
        if session_id_value:
            scoped_owner = await _lookup_account_id([*base_conditions, RequestLog.session_id == session_id_value])
            if scoped_owner is not None:
                return scoped_owner

        return await _lookup_account_id(base_conditions)

    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
        failure_phase: str | None = None,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        bridge_stage: str | None = None,
        request_kind: str = RequestKind.NORMAL.value,
        upstream_proxy_route_mode: str | None = None,
        upstream_proxy_pool_id: str | None = None,
        upstream_proxy_endpoint_id: str | None = None,
        upstream_proxy_fallback_used: bool | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
        provider: str = "openai",
        cache_creation_tokens: int | None = None,
        cache_creation_tier: str | None = None,
        cache_read_tokens: int | None = None,
        identity: RequestIdentity | None = None,
        unified_5h_utilization: float | None = None,
        unified_7d_utilization: float | None = None,
        unified_headers: dict[str, str] | None = None,
        quota_observed_at: datetime | None = None,
        upstream_cost_usd: float | None = None,
    ) -> RequestLog:
        parts = await measured_parts()
        if status == "error":
            upstream_status_code, upstream_error_code = failure_fields(
                upstream_status_code, upstream_error_code, error_code
            )
            error_code = error_code or upstream_error_code
        caller = identity_for_log(identity)
        async with sqlite_writer_section(self._session):
            resolved_request_id = ensure_request_id(request_id)
            resolved_plan_type = plan_type
            if resolved_plan_type is None and account_id:
                resolved_plan_type = await self._resolve_account_plan_type(account_id)
            resolved_useragent = useragent if not isinstance(useragent, str) or useragent.strip() else None
            resolved_useragent_group = (
                useragent_group if not isinstance(useragent_group, str) or useragent_group.strip() else None
            )
            guard = eval_guard_state.get()
            log = RequestLog(
                stripped_tools=json.dumps(guard.removed) if guard is not None and guard.removed else None,
                requested_model_alias=requested_model_alias.get(),
                account_id=account_id,
                provider=provider,
                api_key_id=api_key_id,
                session_id=session_id,
                client_session_id=get_client_session_id(),
                caller_user=caller.caller_user,
                caller_user_source=caller.caller_user_source,
                caller_machine=caller.caller_machine,
                caller_machine_source=caller.caller_machine_source,
                caller_seat=get_caller_seat(),
                room=get_room(),
                lane=get_lane(),
                job_id=get_job_id(),
                dispatch_id=get_dispatch_id(),
                unified_5h_utilization=unified_5h_utilization,
                unified_7d_utilization=unified_7d_utilization,
                unified_headers=unified_headers,
                quota_observed_at=quota_observed_at,
                request_id=resolved_request_id,
                model=model,
                plan_type=resolved_plan_type,
                transport=transport,
                request_kind=request_kind,
                useragent=resolved_useragent,
                useragent_group=resolved_useragent_group,
                service_tier=service_tier,
                requested_service_tier=requested_service_tier,
                actual_service_tier=actual_service_tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cache_creation_tier=cache_creation_tier,
                cache_read_tokens=cache_read_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_usd=None,
                reasoning_effort=reasoning_effort,
                latency_ms=latency_ms,
                latency_first_token_ms=latency_first_token_ms,
                status=status,
                error_code=error_code,
                error_message=error_message,
                failure_phase=failure_phase,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                upstream_status_code=upstream_status_code,
                upstream_error_code=upstream_error_code,
                bridge_stage=bridge_stage,
                upstream_proxy_route_mode=upstream_proxy_route_mode,
                upstream_proxy_pool_id=upstream_proxy_pool_id,
                upstream_proxy_endpoint_id=upstream_proxy_endpoint_id,
                upstream_proxy_fallback_used=upstream_proxy_fallback_used,
                upstream_proxy_fail_closed_reason=upstream_proxy_fail_closed_reason,
                requested_at=requested_at or utcnow(),
            )
            log.cost_usd = (
                upstream_cost_usd
                if upstream_cost_usd is not None
                else calculated_cost_from_log(typing_cast(RequestLogLike, log))
            )
            self._session.add(log)
            try:
                await self._session.commit()
                await self._session.refresh(log)
                await store_context_parts(self._session, log.id, parts)
                return log
            except sa_exc.ResourceClosedError:
                return log
            except BaseException:
                await _safe_rollback(self._session)
                raise

    async def update_model_for_request(self, request_id: str, model: str) -> int:

        async with sqlite_writer_section(self._session):
            resolved_request_id = ensure_request_id(request_id)
            try:
                stmt = select(RequestLog).where(RequestLog.request_id == resolved_request_id)
                result_rows = await self._session.execute(stmt)
                logs = list(result_rows.scalars())
                if not logs:
                    return 0
                for log in logs:
                    log.model = model
                    log.cost_usd = calculated_cost_from_log(typing_cast(RequestLogLike, log))
                await self._session.commit()
            except sa_exc.ResourceClosedError:
                return 0
            except BaseException:
                await _safe_rollback(self._session)
                raise
            return len(logs)

    async def _resolve_account_plan_type(self, account_id: str) -> str | None:
        result = await self._session.execute(select(Account.plan_type).where(Account.id == account_id).limit(1))
        return result.scalar_one_or_none()


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        with anyio.CancelScope(shield=True):
            await session.rollback()
    except BaseException:
        return
