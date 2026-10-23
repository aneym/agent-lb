from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.core.audit.service import AuditService
from app.core.auth.dependencies import require_dashboard_write_access
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError, DashboardNotFoundError
from app.core.middleware.multipart_content_encoding import raise_for_unsupported_multipart_content_encoding
from app.core.multipart import ACCOUNT_IMPORT_MULTIPART_POLICY, bounded_multipart_form, read_bounded_upload
from app.core.multipart_fields import required_upload
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.accounts.errors import InvalidAuthJsonError
from app.modules.accounts.identity_repository import AccountIdentityConflictError
from app.modules.accounts.schemas import (
    AccountApiKeyImportRequest,
    AccountAuthExportResponse,
    AccountExportResponse,
    AccountImportResponse,
    AccountOpenCodeAuthExportResponse,
)

router = APIRouter()
_ACCOUNT_IMPORT_OPENAPI_EXTRA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "title": "Body_import_account_api_accounts_import_post",
                    "required": ["auth_json"],
                    "properties": {
                        "auth_json": {
                            "type": "string",
                            "contentMediaType": "application/octet-stream",
                            "title": "Auth Json",
                        }
                    },
                }
            }
        },
    },
    "responses": {
        "422": {
            "description": "Validation Error",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}},
        }
    },
}


@router.post("/{account_id}/export", response_model=AccountExportResponse, deprecated=True)
async def export_account(
    request: Request,
    response: Response,
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountExportResponse:
    result = await context.service.export_account(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    AuditService.log_async(
        "account_exported",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": result.account_id},
    )
    return result


@router.post("/{account_id}/export/auth", response_model=AccountAuthExportResponse)
async def export_account_auth(
    request: Request,
    response: Response,
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountAuthExportResponse:
    result = await context.service.export_auth(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    AuditService.log_async(
        "account_auth_exported",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id},
    )
    return result


@router.post("/{account_id}/export/opencode-auth", response_model=AccountOpenCodeAuthExportResponse, deprecated=True)
async def export_account_opencode_auth(
    request: Request,
    response: Response,
    account_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountOpenCodeAuthExportResponse:
    result = await context.service.export_opencode_auth(account_id)
    if not result:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    AuditService.log_async(
        "account_auth_exported",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": account_id},
    )
    return result


@router.post("/import", response_model=AccountImportResponse, openapi_extra=_ACCOUNT_IMPORT_OPENAPI_EXTRA)
async def import_account(
    request: Request,
    _write_access=Depends(require_dashboard_write_access),
    context: AccountsContext = Depends(get_accounts_context),
) -> AccountImportResponse:
    raise_for_unsupported_multipart_content_encoding(request)
    async with bounded_multipart_form(
        request, ACCOUNT_IMPORT_MULTIPART_POLICY, typed_upload_fields=("auth_json",)
    ) as form:
        auth_json = required_upload(form, "auth_json")
        raw = await read_bounded_upload(
            auth_json, max_bytes=ACCOUNT_IMPORT_MULTIPART_POLICY.max_file_bytes, param="auth_json"
        )
    try:
        response = await context.service.import_account(raw)
        AuditService.log_async(
            "account_created",
            actor_ip=request.client.host if request.client else None,
            details={"account_id": response.account_id},
        )
        return response
    except InvalidAuthJsonError as exc:
        raise DashboardBadRequestError("Invalid auth.json payload", code="invalid_auth_json") from exc
    except AccountIdentityConflictError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_identity_conflict") from exc


@router.post("/import/api-key", response_model=AccountImportResponse)
async def import_api_key_account(
    payload: AccountApiKeyImportRequest, request: Request, context: AccountsContext = Depends(get_accounts_context)
) -> AccountImportResponse:
    try:
        response = await context.service.import_api_key_account(payload)
    except ValueError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_api_key_account") from exc
    except AccountIdentityConflictError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_identity_conflict") from exc
    AuditService.log_async(
        "account_created",
        actor_ip=request.client.host if request.client else None,
        details={"account_id": response.account_id, "provider": payload.provider},
    )
    return response
