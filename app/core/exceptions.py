from __future__ import annotations

from datetime import datetime, timezone


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    message: str = "Unexpected error"

    def __init__(self, message: str | None = None, *, code: str | None = None, param: str | None = None) -> None:
        self.message = message or self.__class__.message
        self.param = param
        if code is not None:
            self.code = code
        super().__init__(self.message)


class ProxyAuthError(AppError):
    status_code = 401
    code = "invalid_api_key"
    error_type = "authentication_error"


class ProxyModelNotAllowed(AppError):
    status_code = 403
    code = "model_not_allowed"
    error_type = "permission_error"


class ProxyReasoningEffortNotAllowed(AppError):
    status_code = 403
    code = "reasoning_effort_not_allowed"
    error_type = "permission_error"


class ProxyRateLimitError(AppError):
    status_code = 429
    code = "rate_limit_exceeded"
    error_type = "rate_limit_error"


class ProxyUpstreamError(AppError):
    status_code = 503
    code = "upstream_error"
    error_type = "server_error"


class ProxyRequiredCapabilityTransportError(AppError):
    status_code = 400
    code = "required_capability_transport_unsupported"
    error_type = "invalid_request_error"
    message = "Required capability routing is only supported over the Responses WebSocket transport."


class TeamMemberSuspendedError(AppError):
    status_code = 403
    code = "team_member_suspended"
    error_type = "team_member_suspended"


class TeamModelNotAllowedError(AppError):
    status_code = 403
    code = "team_model_not_allowed"
    error_type = "team_model_not_allowed"


class TeamMemberOverCapError(AppError):
    status_code = 429
    code = "team_member_over_cap"
    error_type = "team_member_over_cap"

    def __init__(self, message: str, *, window: str, reset_at: datetime, code: str | None = None) -> None:
        self.window = window
        self.reset_at = reset_at
        super().__init__(message, code=code)

    @property
    def headers(self) -> dict[str, str]:
        reset_at = self.reset_at if self.reset_at.tzinfo is not None else self.reset_at.replace(tzinfo=timezone.utc)
        return {"X-Team-Window": self.window, "X-Team-Reset": reset_at.isoformat().replace("+00:00", "Z")}


class DashboardAuthError(AppError):
    status_code = 401
    code = "authentication_required"


class DashboardPermissionError(AppError):
    status_code = 403
    code = "permission_denied"


class DashboardForbiddenError(DashboardAuthError):
    status_code = 403
    code = "forbidden"


class DashboardNotFoundError(AppError):
    status_code = 404
    code = "not_found"


class DashboardConflictError(AppError):
    status_code = 409
    code = "conflict"


class DashboardSettingsConflictError(DashboardConflictError):
    code = "settings_conflict"
    message = "Settings were modified by another writer; reload and retry"


class DashboardBadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class DashboardValidationError(AppError):
    status_code = 422
    code = "validation_error"


class DashboardRateLimitError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int, code: str | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message, code=code)


class DashboardUpstreamError(AppError):
    status_code = 502
    code = "upstream_error"


class DashboardServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
