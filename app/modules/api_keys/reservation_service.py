from __future__ import annotations

import asyncio

from sqlalchemy.exc import OperationalError

from app.db.models import LimitType
from app.db.session import sqlite_writer_section
from app.modules.api_keys.key_validation import _ensure_valid_api_key_row, _lazy_reset_expired_limits
from app.modules.api_keys.repository_data import UsageReservationItemData
from app.modules.api_keys.repository_protocol import ApiKeysRepositoryProtocol
from app.modules.api_keys.reservation_pricing import _calculate_cost_microdollars, _compute_increment_for_limit_type, _is_sqlite_database_locked, _limit_applies_for_request, _model_has_list_price, _next_usage_reservation_id, _normalize_request_usage_budget, _rate_limit_exceeded_error, _reserve_delta_for_limit  # noqa: I001  # fmt: skip
from app.modules.api_keys.service_data import _SQLITE_BUSY_RETRY_ATTEMPTS, _SQLITE_BUSY_RETRY_BASE_SECONDS, ApiKeyInvalidError, ApiKeyRequestUsageBudget, ApiKeyUnpricedModelError, ApiKeyUsageReservationData  # noqa: I001  # fmt: skip


class ApiKeyReservationService:
    _repository: ApiKeysRepositoryProtocol

    async def enforce_limits_for_request(
        self,
        key_id: str,
        *,
        request_model: str | None,
        request_service_tier: str | None = None,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
        request_is_file_operation: bool = False,
    ) -> ApiKeyUsageReservationData:
        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                return await self._enforce_limits_for_request_once(
                    key_id,
                    request_model=request_model,
                    request_service_tier=request_service_tier,
                    request_usage_budget=request_usage_budget,
                    request_is_file_operation=request_is_file_operation,
                )
            except OperationalError as exc:
                await self._repository.rollback()
                if not _is_sqlite_database_locked(exc) or attempt == _SQLITE_BUSY_RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_SQLITE_BUSY_RETRY_BASE_SECONDS * (2**attempt))

        raise RuntimeError("unreachable")

    async def _enforce_limits_for_request_once(
        self,
        key_id: str,
        *,
        request_model: str | None,
        request_service_tier: str | None,
        request_usage_budget: ApiKeyRequestUsageBudget | None,
        request_is_file_operation: bool = False,
    ) -> ApiKeyUsageReservationData:
        now = self._now()
        async with sqlite_writer_section(self._repository.session):
            row = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id))
            if row.expires_at is not None and row.expires_at < now:
                raise ApiKeyInvalidError("API key has expired")
            limits_reset = await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
            refreshed = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id)) if limits_reset else row
            if refreshed.expires_at is not None and refreshed.expires_at < now:
                raise ApiKeyInvalidError("API key has expired")

            reservation_items: list[UsageReservationItemData] = []
            normalized_usage_budget = _normalize_request_usage_budget(request_usage_budget)
            try:
                for limit in refreshed.limits:
                    if (
                        request_is_file_operation
                        and request_model in {"files-create", "files-finalize"}
                        and limit.limit_type == LimitType.COST_USD
                    ):
                        continue
                    if not _limit_applies_for_request(limit, request_model=request_model):
                        continue
                    if (
                        limit.limit_type == LimitType.COST_USD
                        and request_model is not None
                        and not _model_has_list_price(request_model)
                    ):
                        raise ApiKeyUnpricedModelError(
                            model=request_model,
                            limit_window=limit.limit_window.value,
                            max_microdollars=limit.max_value,
                        )
                    if limit.current_value >= limit.max_value:
                        raise _rate_limit_exceeded_error(limit)
                    reserve_delta = _reserve_delta_for_limit(
                        limit,
                        request_model=request_model,
                        request_service_tier=request_service_tier,
                        request_usage_budget=normalized_usage_budget,
                    )
                    if reserve_delta > 0:
                        result = await self._repository.try_reserve_usage(
                            limit.id,
                            delta=reserve_delta,
                            expected_reset_at=limit.reset_at,
                        )
                        if not result.success:
                            raise _rate_limit_exceeded_error(limit)
                    reservation_items.append(
                        UsageReservationItemData(
                            limit_id=limit.id,
                            limit_type=limit.limit_type,
                            reserved_delta=reserve_delta,
                            expected_reset_at=limit.reset_at,
                        )
                    )

                reservation_id = _next_usage_reservation_id()
                await self._repository.create_usage_reservation(
                    reservation_id,
                    key_id=key_id,
                    model=request_model or "",
                    items=reservation_items,
                )
                await self._repository.commit()
            except Exception:
                await self._repository.rollback()
                raise

        return ApiKeyUsageReservationData(
            reservation_id=reservation_id,
            key_id=key_id,
            model=request_model or "",
        )

    async def finalize_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        service_tier: str | None = None,
        cache_creation_input_tokens: int | None = None,
        cache_read_input_tokens: int | None = None,
        cache_creation_tier: str | None = None,
        cache_creation_1h_input_tokens: int | None = None,
    ) -> None:
        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                await self._settle_usage_reservation(
                    reservation_id,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    service_tier=service_tier,
                    status="finalized",
                    cache_creation_input_tokens=cache_creation_input_tokens,
                    cache_read_input_tokens=cache_read_input_tokens,
                    cache_creation_tier=cache_creation_tier,
                    cache_creation_1h_input_tokens=cache_creation_1h_input_tokens,
                )
                return
            except OperationalError as exc:
                await self._repository.rollback()
                if not _is_sqlite_database_locked(exc) or attempt == _SQLITE_BUSY_RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_SQLITE_BUSY_RETRY_BASE_SECONDS * (2**attempt))

        raise RuntimeError("unreachable")

    async def fail_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        service_tier: str | None = None,
    ) -> None:
        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                await self._settle_usage_reservation(
                    reservation_id,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    service_tier=service_tier,
                    status="failed",
                )
                return
            except OperationalError as exc:
                await self._repository.rollback()
                if not _is_sqlite_database_locked(exc) or attempt == _SQLITE_BUSY_RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_SQLITE_BUSY_RETRY_BASE_SECONDS * (2**attempt))

        raise RuntimeError("unreachable")

    async def _settle_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        service_tier: str | None,
        status: str,
        cache_creation_input_tokens: int | None = None,
        cache_read_input_tokens: int | None = None,
        cache_creation_tier: str | None = None,
        cache_creation_1h_input_tokens: int | None = None,
    ) -> None:
        async with sqlite_writer_section(self._repository.session):
            reservation = await self._repository.get_usage_reservation(reservation_id)
            if reservation is None or reservation.status != "reserved":
                return

            claimed = await self._repository.transition_usage_reservation_status(
                reservation_id,
                expected_status="reserved",
                new_status="settling",
            )
            if not claimed:
                await self._repository.rollback()
                return

            effective_input_tokens = input_tokens or 0
            effective_output_tokens = output_tokens or 0
            effective_cached_input_tokens = cached_input_tokens or 0
            cost_microdollars = _calculate_cost_microdollars(
                model,
                effective_input_tokens,
                effective_output_tokens,
                effective_cached_input_tokens,
                service_tier,
                cache_creation_input_tokens=cache_creation_input_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_creation_tier=cache_creation_tier,
                cache_creation_1h_input_tokens=cache_creation_1h_input_tokens,
            )

            consumed_tokens = (
                effective_input_tokens
                + effective_output_tokens
                + effective_cached_input_tokens
                + (cache_creation_input_tokens or 0)
                + (cache_read_input_tokens or 0)
            )
            unpriced_usage = cost_microdollars is None and consumed_tokens > 0

            try:
                for item in reservation.items:
                    if item.limit_type == LimitType.COST_USD and unpriced_usage:
                        actual_delta = item.reserved_delta
                    else:
                        actual_delta = _compute_increment_for_limit_type(
                            item.limit_type,
                            input_tokens=effective_input_tokens,
                            output_tokens=effective_output_tokens,
                            cost_microdollars=cost_microdollars or 0,
                        )
                    delta = actual_delta - item.reserved_delta
                    if delta != 0:
                        await self._repository.adjust_reserved_usage(
                            item.limit_id,
                            delta=delta,
                            expected_reset_at=item.expected_reset_at,
                        )
                    await self._repository.upsert_reservation_item_actual(
                        reservation_id,
                        item=item,
                        actual_delta=actual_delta,
                    )

                await self._repository.settle_usage_reservation(
                    reservation_id,
                    status=status,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    cost_microdollars=cost_microdollars,
                )
                await self._repository.update_last_used(reservation.api_key_id, commit=False)
                await self._repository.commit()
            except Exception:
                await self._repository.rollback()
                raise

    async def touch_usage_reservation(self, reservation_id: str) -> bool:
        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                async with sqlite_writer_section(self._repository.session):
                    touched = await self._repository.touch_usage_reservation(reservation_id)
                    await self._repository.commit()
                    return touched
            except OperationalError as exc:
                await self._repository.rollback()
                if not _is_sqlite_database_locked(exc) or attempt == _SQLITE_BUSY_RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_SQLITE_BUSY_RETRY_BASE_SECONDS * (2**attempt))

        raise RuntimeError("unreachable")

    async def release_usage_reservation(self, reservation_id: str) -> None:
        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                await self._release_usage_reservation_once(reservation_id)
                return
            except OperationalError as exc:
                await self._repository.rollback()
                if not _is_sqlite_database_locked(exc) or attempt == _SQLITE_BUSY_RETRY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_SQLITE_BUSY_RETRY_BASE_SECONDS * (2**attempt))

        raise RuntimeError("unreachable")

    async def _release_usage_reservation_once(self, reservation_id: str) -> None:
        async with sqlite_writer_section(self._repository.session):
            reservation = await self._repository.get_usage_reservation(reservation_id)
            if reservation is None or reservation.status != "reserved":
                return

            claimed = await self._repository.transition_usage_reservation_status(
                reservation_id,
                expected_status="reserved",
                new_status="released",
            )
            if not claimed:
                await self._repository.rollback()
                return

            try:
                for item in reservation.items:
                    await self._repository.adjust_reserved_usage(
                        item.limit_id,
                        delta=-item.reserved_delta,
                        expected_reset_at=item.expected_reset_at,
                    )
                    await self._repository.upsert_reservation_item_actual(
                        reservation_id,
                        item=item,
                        actual_delta=0,
                    )
                await self._repository.settle_usage_reservation(
                    reservation_id,
                    status="released",
                    input_tokens=None,
                    output_tokens=None,
                    cached_input_tokens=None,
                    cost_microdollars=None,
                )
                await self._repository.commit()
            except Exception:
                await self._repository.rollback()
                raise

    async def record_usage(
        self,
        key_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        service_tier: str | None = None,
    ) -> None:
        cost_microdollars = _calculate_cost_microdollars(
            model,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            service_tier,
        )
        await self._repository.increment_limit_usage(
            key_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_microdollars=cost_microdollars or 0,
        )
