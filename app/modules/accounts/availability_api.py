from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.resilience.degradation import get_status as get_degradation_status
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.schemas import (
    AccountSummary,
    AnthropicModelsResponse,
    AvailabilityResponse,
    AvailabilityUnavailableAccount,
    DegradationStatus,
    ProviderAvailability,
)

availability_router = APIRouter(
    prefix="/api",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


_AVAILABLE_ACCOUNT_STATUSES = {"active"}


def _account_recovery_at(account: AccountSummary):
    candidates = [moment for moment in (account.rate_limit_reset_at, account.reset_at_primary) if moment is not None]
    return min(candidates) if candidates else None


def build_availability_response(accounts: list[AccountSummary]) -> AvailabilityResponse:
    providers: dict[str, ProviderAvailability] = {}
    grouped: dict[str, list[AccountSummary]] = {}
    for account in accounts:
        grouped.setdefault(account.provider, []).append(account)
    for provider, rows in sorted(grouped.items()):
        unavailable = [row for row in rows if row.status not in _AVAILABLE_ACCOUNT_STATUSES]
        recoveries = [
            recovery for recovery in (_account_recovery_at(row) for row in unavailable) if recovery is not None
        ]
        providers[provider] = ProviderAvailability(
            total=len(rows),
            available=len(rows) - len(unavailable),
            unavailable=[
                AvailabilityUnavailableAccount(
                    id=row.account_id,
                    status=row.status,
                    reset_at=_account_recovery_at(row),
                )
                for row in unavailable
            ],
            earliest_recovery_at=min(recoveries) if recoveries else None,
        )
    status = get_degradation_status()
    return AvailabilityResponse(
        degradation=DegradationStatus(level=status.get("level") or "normal", reason=status.get("reason")),
        providers=providers,
    )


@availability_router.get("/models/anthropic", response_model=AnthropicModelsResponse)
async def list_anthropic_models(
    context: AccountsContext = Depends(get_accounts_context),
) -> AnthropicModelsResponse:

    return await context.service.list_anthropic_models()


@availability_router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    context: AccountsContext = Depends(get_accounts_context),
) -> AvailabilityResponse:

    accounts = await context.service.list_accounts(include_request_usage=False)
    return build_availability_response(accounts)
