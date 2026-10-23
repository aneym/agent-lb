from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field

from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    RateLimitResetCreditsSnapshot,
    ResetCreditFetchError,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.core.exceptions import (
    DashboardAuthError,
    DashboardConflictError,
    DashboardPermissionError,
    DashboardServiceUnavailableError,
)
from app.modules.shared.schemas import DashboardModel


class ResetCreditItemResponse(DashboardModel):
    id: str
    reset_type: str | None = None
    status: str | None = None
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    title: str | None = None
    description: str | None = None
    redeem_started_at: datetime | None = None
    redeemed_at: datetime | None = None


class RateLimitResetCreditsSnapshotResponse(DashboardModel):
    available_count: int = 0
    nearest_expires_at: datetime | None = None
    credits: list[ResetCreditItemResponse] = Field(default_factory=list)


class ConsumeResetCreditResponseSchema(DashboardModel):
    code: str | None = None
    windows_reset: int | None = None
    redeemed_at: datetime | None = None


def _translate_fetch_error(exc: ResetCreditFetchError) -> Exception:
    if exc.status_code == 401:
        return DashboardAuthError(exc.message, code=exc.code)
    if exc.status_code == 403:
        return DashboardPermissionError(exc.message, code=exc.code)
    if exc.status_code == 409:
        return DashboardConflictError(exc.message, code=exc.code)
    return DashboardServiceUnavailableError(exc.message, code=exc.code)


def _translate_consume_error(exc: ConsumeResetCreditError) -> Exception:
    if exc.status_code == 401:
        return DashboardAuthError(exc.message, code=exc.code)
    if exc.status_code == 403:
        return DashboardPermissionError(exc.message, code=exc.code)
    if exc.status_code == 409:
        return DashboardConflictError(exc.message, code=exc.code)
    return DashboardServiceUnavailableError(exc.message, code=exc.code)


def _select_soonest_available_credit(
    snapshot: RateLimitResetCreditsSnapshot | None,
) -> ResetCreditItem | None:
    if snapshot is None:
        return None
    return _select_soonest_available_credit_from_items(snapshot.credits, snapshot.available_count)


def _select_soonest_available_credit_from_response(
    response: ResetCreditsResponse,
) -> ResetCreditItem | None:
    return _select_soonest_available_credit_from_items(response.credits, response.available_count)


def _select_available_credit_by_id(
    response: ResetCreditsResponse,
    credit_id: str,
) -> ResetCreditItem | None:
    if response.available_count <= 0:
        return None
    for credit in response.credits:
        if credit.id == credit_id and credit.status == "available":
            return credit
    return None


def _same_credit_expiry(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    else:
        left = left.astimezone(timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    else:
        right = right.astimezone(timezone.utc)
    return left == right


def _select_soonest_available_credit_from_items(
    credits: list[ResetCreditItem],
    available_count: int,
) -> ResetCreditItem | None:
    if available_count <= 0:
        return None
    available = [credit for credit in credits if credit.status == "available"]
    if not available:
        return None
    far_future = datetime.max.replace(tzinfo=timezone.utc)
    return min(available, key=lambda credit: credit.expires_at or far_future)


def _snapshot_to_response(
    snapshot: RateLimitResetCreditsSnapshot | None,
) -> RateLimitResetCreditsSnapshotResponse | None:
    if snapshot is None:
        return None
    return RateLimitResetCreditsSnapshotResponse(
        available_count=snapshot.available_count,
        nearest_expires_at=snapshot.nearest_expires_at,
        credits=[ResetCreditItemResponse.model_validate(credit.model_dump()) for credit in snapshot.credits],
    )
