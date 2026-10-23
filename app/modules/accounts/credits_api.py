from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.audit.service import AuditService
from app.core.auth.dependencies import require_dashboard_write_access
from app.core.auth.refresh import RefreshError
from app.core.clients.rate_limit_resets import ResetCreditsError
from app.core.clients.usage import UsageFetchError
from app.core.exceptions import DashboardConflictError, DashboardNotFoundError, DashboardUpstreamError
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.schemas import (
    AccountResetCreditConsumeRequest,
    AccountResetCreditConsumeResponse,
    AccountResetCreditsResponse,
    AccountUsageResetConsumeRequest,
    AccountUsageResetConsumeResponse,
    AccountUsageResetCreditsResponse,
)
from app.modules.accounts.service import (
    AccountResetCreditsUnavailableError,
    AccountUsageResetConsumeUnavailableError,
    AccountUsageResetCreditsUnavailableError,
)

router = APIRouter()


@router.get("/{account_id}/usage-reset-credits", response_model=AccountUsageResetCreditsResponse)
async def get_account_usage_reset_credits(
    account_id: str, context: AccountsContext = Depends(get_accounts_context)
) -> AccountUsageResetCreditsResponse:
    try:
        result = await context.service.get_usage_reset_credits(account_id)
    except AccountUsageResetCreditsUnavailableError as exc:
        raise DashboardConflictError(str(exc), code="account_usage_reset_credits_unavailable") from exc
    except UpstreamProxyRouteError as exc:
        raise DashboardUpstreamError(
            f"Unable to resolve upstream proxy route for usage reset credits: {exc.reason}",
            code="upstream_proxy_unavailable",
        ) from exc
    except UsageFetchError as exc:
        raise DashboardUpstreamError(
            f"Usage reset credits fetch failed: {exc.message}", code="usage_reset_credits_fetch_failed"
        ) from exc
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.post("/{account_id}/usage-reset-credits/consume", response_model=AccountUsageResetConsumeResponse)
async def consume_account_usage_reset_credit(
    request: Request,
    account_id: str,
    payload: AccountUsageResetConsumeRequest | None = None,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountUsageResetConsumeResponse:
    try:
        result = await context.service.consume_usage_reset_credit(
            account_id, redeem_request_id=payload.redeem_request_id if payload is not None else None
        )
    except AccountUsageResetConsumeUnavailableError as exc:
        raise DashboardConflictError(str(exc), code="account_usage_reset_consume_unavailable") from exc
    except UpstreamProxyRouteError as exc:
        raise DashboardUpstreamError(
            f"Unable to resolve upstream proxy route for usage reset: {exc.reason}", code="upstream_proxy_unavailable"
        ) from exc
    except UsageFetchError as exc:
        raise DashboardUpstreamError(
            f"Usage reset consume failed: {exc.message}", code="usage_reset_consume_failed"
        ) from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_usage_reset_consumed",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": result.account_id,
            "code": result.code,
            "windows_reset": result.windows_reset,
            "usage_written": result.usage_written,
        },
    )
    return result


async def list_account_reset_credits(
    account_id: str, context: AccountsContext = Depends(get_accounts_context)
) -> AccountResetCreditsResponse:
    try:
        result = await context.service.list_rate_limit_reset_credits(account_id)
    except AccountResetCreditsUnavailableError as exc:
        raise DashboardConflictError(str(exc), code="account_reset_credits_unavailable") from exc
    except RefreshError as exc:
        raise DashboardConflictError(
            f"Reset-credit listing could not refresh account credentials: {exc.message}",
            code="account_reset_credits_refresh_failed",
        ) from exc
    except ResetCreditsError as exc:
        raise DashboardConflictError(
            f"Upstream reset-credit listing failed: {exc.message}", code="account_reset_credits_upstream_error"
        ) from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


async def consume_account_reset_credit(
    request: Request,
    account_id: str,
    body: AccountResetCreditConsumeRequest | None = None,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountResetCreditConsumeResponse:
    credit_id = body.credit_id if body is not None else None
    override_daily_limit = body.override_daily_limit if body is not None else False
    try:
        result = await context.service.redeem_rate_limit_reset_credit(
            account_id, credit_id=credit_id, override_daily_limit=override_daily_limit
        )
    except AccountResetCreditsUnavailableError as exc:
        raise DashboardConflictError(str(exc), code="account_reset_credits_unavailable") from exc
    except RefreshError as exc:
        raise DashboardConflictError(
            f"Reset-credit redemption could not refresh account credentials: {exc.message}",
            code="account_reset_credits_refresh_failed",
        ) from exc
    except ResetCreditsError as exc:
        raise DashboardConflictError(
            f"Upstream reset-credit redemption failed: {exc.message}", code="account_reset_credits_upstream_error"
        ) from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_reset_credit_redeemed",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": account_id,
            "credit_id": credit_id,
            "override_daily_limit": override_daily_limit,
            "code": result.code,
            "windows_reset": result.windows_reset,
        },
    )
    return result
