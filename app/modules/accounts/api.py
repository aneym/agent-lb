from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request

from app.core.audit.service import AuditService
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.auth.refresh import RefreshError
from app.core.config.settings import get_settings
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError, DashboardNotFoundError
from app.dependencies import AccountsContext, get_accounts_context, get_proxy_service_for_app
from app.modules.accounts.auth_manager import AccountNotOwnedError
from app.modules.accounts.credits_api import consume_account_reset_credit as consume_account_reset_credit
from app.modules.accounts.credits_api import consume_account_usage_reset_credit as consume_account_usage_reset_credit
from app.modules.accounts.credits_api import get_account_usage_reset_credits as get_account_usage_reset_credits
from app.modules.accounts.credits_api import list_account_reset_credits as list_account_reset_credits
from app.modules.accounts.credits_api import router as credits_api_router
from app.modules.accounts.errors import AccountNotProbableError, AccountStateTransitionError
from app.modules.accounts.refresh_lock import _cross_process_refresh_lock
from app.modules.accounts.schemas import (
    AccountAliasRequest,
    AccountAliasResponse,
    AccountDeleteResponse,
    AccountLimitWarmupUpdateRequest,
    AccountLimitWarmupUpdateResponse,
    AccountPauseResponse,
    AccountProbeRequest,
    AccountProbeResponse,
    AccountReactivateResponse,
    AccountRoutingPolicyUpdateRequest,
    AccountRoutingPolicyUpdateResponse,
    AccountsResponse,
    AccountSubscriptionCheckResponse,
    AccountSubscriptionUpdateRequest,
    AccountSubscriptionUpdateResponse,
    AccountTrendsResponse,
    AccountUpdateRequest,
    AccountUpdateResponse,
)
from app.modules.accounts.transfer_api import _ACCOUNT_IMPORT_OPENAPI_EXTRA as _ACCOUNT_IMPORT_OPENAPI_EXTRA
from app.modules.accounts.transfer_api import export_account as export_account
from app.modules.accounts.transfer_api import export_account_auth as export_account_auth
from app.modules.accounts.transfer_api import export_account_opencode_auth as export_account_opencode_auth
from app.modules.accounts.transfer_api import import_account as import_account
from app.modules.accounts.transfer_api import import_api_key_account as import_api_key_account
from app.modules.federation.push_receiver import load_binding

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/accounts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=AccountsResponse)
async def list_accounts(
    fresh: bool = False, context: AccountsContext = Depends(get_accounts_context)
) -> AccountsResponse:
    state_path = get_settings().federation_push_sources_path.with_name("federation-push-state.json")
    try:
        source_bindings = load_binding(state_path)
    except (OSError, ValueError, TypeError):
        source_bindings = {}
    owner_source_names = {instance_id: source for source, instance_id in source_bindings.items()}
    accounts = await context.service.list_accounts(include_request_usage=fresh, owner_source_names=owner_source_names)
    return AccountsResponse(accounts=accounts)


@router.get("/{account_id}/trends", response_model=AccountTrendsResponse)
async def get_account_trends(
    account_id: str, context: AccountsContext = Depends(get_accounts_context)
) -> AccountTrendsResponse:
    result = await context.service.get_account_trends(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.post("/{account_id}/exchange-reset")
async def reset_uncertain_exchange(
    account_id: str, context: AccountsContext = Depends(get_accounts_context)
) -> dict[str, str]:
    settings = get_settings()
    timeout_seconds = max(settings.oauth_timeout_seconds, settings.token_refresh_timeout_seconds, 30.0)
    async with _cross_process_refresh_lock(account_id):
        outcome = await context.repository.clear_exchange_intent(account_id, timeout_seconds)
        if isinstance(outcome, datetime):
            raise DashboardConflictError(f"Exchange reset becomes available at {outcome.isoformat()} UTC")
        if not outcome:
            raise DashboardConflictError("Account is not exchange-uncertain or its tokens were lost; re-login required")
    return {
        "account_id": account_id,
        "status": "active",
        "message": "Next refresh replays the token once; re-login may be required",
    }


@router.post("/{account_id}/reactivate", response_model=AccountReactivateResponse)
async def reactivate_account(
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountReactivateResponse:
    try:
        success = await context.service.reactivate_account(account_id)
    except AccountStateTransitionError as exc:
        raise DashboardConflictError(str(exc), code="account_state_transition_invalid") from exc
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountReactivateResponse(status="reactivated")


@router.patch("/{account_id}", response_model=AccountUpdateResponse)
async def update_account(
    account_id: str,
    payload: AccountUpdateRequest,
    request: Request,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountUpdateResponse:
    changed_fields = [field for field, value in payload.model_dump(exclude_unset=True).items() if value is not None]
    if not changed_fields:
        raise DashboardBadRequestError("No supported account fields to update", code="empty_account_update")
    success = await context.service.update_account(
        account_id, security_work_authorized=payload.security_work_authorized
    )
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_updated",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id, "changed_fields": changed_fields},
    )
    return AccountUpdateResponse(status="updated")


@router.post("/{account_id}/probe", response_model=AccountProbeResponse)
async def probe_account(
    request: Request,
    account_id: str,
    body: AccountProbeRequest | None = None,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountProbeResponse:
    requested_model = body.model if body is not None else None
    try:
        result = await context.service.probe_account(account_id, model=requested_model)
    except AccountNotOwnedError as exc:
        raise DashboardConflictError(str(exc), code="account_not_owned") from exc
    except AccountNotProbableError as exc:
        raise DashboardConflictError(str(exc), code="account_not_probable") from exc
    except RefreshError as exc:
        raise DashboardConflictError(
            f"Probe could not refresh account credentials: {exc.message}", code="account_probe_refresh_failed"
        ) from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    account = await context.repository.get_by_id(account_id)
    if account is not None and account.provider == "openai":
        probe_succeeded = 200 <= result.probe_status_code < 300
        if not probe_succeeded or result.usage_refresh_ready_for_probe_settlement():
            try:
                await get_proxy_service_for_app(request.app).record_account_probe_result(
                    account_id=result.account_id, http_status=result.probe_status_code
                )
            except Exception:
                logger.exception(
                    "Force Probe advisory settlement failed account_id=%s probe_status_code=%s",
                    result.account_id,
                    result.probe_status_code,
                )
        else:
            logger.warning(
                "Force Probe success skipped advisory settlement before successful usage refresh fetch account_id=%s probe_status_code=%s",
                result.account_id,
                result.probe_status_code,
            )
    elif result.probe_status_code == 200:
        account = await context.repository.get_by_id(account_id)
        service = getattr(request.app.state, "anthropic_proxy_service", None)
        if account is not None and account.provider != "openai" and (service is not None):
            await service._load_balancer.record_success(account)
    AuditService.log_async(
        "account_probed",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": result.account_id,
            "probe_status_code": result.probe_status_code,
            "model": requested_model,
        },
    )
    return result


@router.post("/{account_id}/pause", response_model=AccountPauseResponse)
async def pause_account(
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountPauseResponse:
    try:
        success = await context.service.pause_account(account_id)
    except AccountStateTransitionError as exc:
        raise DashboardConflictError(str(exc), code="account_state_transition_invalid") from exc
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountPauseResponse(status="paused")


@router.put("/{account_id}/alias", response_model=AccountAliasResponse)
async def set_account_alias(
    account_id: str,
    payload: AccountAliasRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountAliasResponse:
    success = await context.service.set_account_alias(account_id, payload.alias)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    normalized = payload.alias.strip() if isinstance(payload.alias, str) else None
    if normalized == "":
        normalized = None
    return AccountAliasResponse(account_id=account_id, alias=normalized)


@router.put("/{account_id}/limit-warmup", response_model=AccountLimitWarmupUpdateResponse)
async def update_account_limit_warmup(
    account_id: str,
    payload: AccountLimitWarmupUpdateRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountLimitWarmupUpdateResponse:
    success = await context.service.set_limit_warmup_enabled(account_id, payload.enabled)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountLimitWarmupUpdateResponse(
        status="enabled" if payload.enabled else "disabled", enabled=payload.enabled
    )


@router.put("/{account_id}/routing-policy", response_model=AccountRoutingPolicyUpdateResponse)
async def update_account_routing_policy(
    account_id: str,
    payload: AccountRoutingPolicyUpdateRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountRoutingPolicyUpdateResponse:
    success = await context.service.set_routing_policy(account_id, payload.routing_policy)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return AccountRoutingPolicyUpdateResponse(account_id=account_id, routing_policy=payload.routing_policy)


@router.post("/{account_id}/subscription/check", response_model=AccountSubscriptionCheckResponse)
async def check_account_subscription(
    request: Request, account_id: str, context: AccountsContext = Depends(get_accounts_context)
) -> AccountSubscriptionCheckResponse:
    try:
        result = await context.service.check_subscription(account_id)
    except RefreshError as exc:
        raise DashboardConflictError(
            f"Subscription check could not refresh account credentials: {exc.message}",
            code="account_subscription_check_refresh_failed",
        ) from exc
    except AccountStateTransitionError as exc:
        raise DashboardConflictError(str(exc), code="account_subscription_check_invalid") from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_subscription_checked",
        actor_ip=request.client.host if request.client else None,
        details={
            "account_id": result.account_id,
            "working": result.working,
            "probe_status_code": result.probe_status_code,
        },
    )
    return result


@router.put("/{account_id}/subscription", response_model=AccountSubscriptionUpdateResponse)
async def update_account_subscription(
    request: Request,
    account_id: str,
    payload: AccountSubscriptionUpdateRequest,
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountSubscriptionUpdateResponse:
    subscription = await context.service.set_subscription_ledger(account_id, payload)
    if subscription is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_subscription_ledger_updated",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id, "subscription_status": subscription.status},
    )
    return AccountSubscriptionUpdateResponse(account_id=account_id, subscription=subscription)


@router.delete("/{account_id}", response_model=AccountDeleteResponse)
async def delete_account(
    request: Request,
    account_id: str,
    delete_history: bool = False,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountDeleteResponse:
    success = await context.service.delete_account(account_id, delete_history=delete_history)
    if not success:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_deleted",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id, "delete_history": delete_history},
    )
    return AccountDeleteResponse(status="deleted")


router.include_router(credits_api_router)

from app.modules.accounts.transfer_api import router as transfer_api_router

router.include_router(transfer_api_router)
