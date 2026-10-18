from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

_SQLITE_BUSY_RETRY_ATTEMPTS = 4


_SQLITE_BUSY_RETRY_BASE_SECONDS = 0.1


_SPARKLINE_DAYS = 7


_DETAIL_BUCKET_SECONDS = 3600


API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET = 8_192


API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS = API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET


API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS = 2_048


_API_KEY_USAGE_RESERVATION_UNKNOWN_MODEL_MICRODOLLARS = 2_000_000


_API_KEY_USAGE_RESERVATION_UNKNOWN_MODEL_BASE_TOKENS = API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET * 2


TRAFFIC_CLASS_FOREGROUND = "foreground"


TRAFFIC_CLASS_OPPORTUNISTIC = "opportunistic"


_SUPPORTED_TRAFFIC_CLASSES = frozenset({TRAFFIC_CLASS_FOREGROUND, TRAFFIC_CLASS_OPPORTUNISTIC})


_SUPPORTED_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


_SUPPORTED_SERVICE_TIERS = frozenset({"auto", "default", "priority", "flex"})


class ApiKeyNotFoundError(ValueError):
    pass


class ApiKeyInvalidError(ValueError):
    pass


class ApiKeyValidationError(ValueError):
    pass


class MemberKeyScopeRequiredError(ApiKeyValidationError):
    pass


class ApiKeyRateLimitExceededError(ValueError):
    def __init__(self, *, message: str, reset_at: datetime) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class ApiKeyUnpricedModelError(ValueError):
    def __init__(self, *, model: str, limit_window: str, max_microdollars: int) -> None:
        self.model = model
        self.limit_window = limit_window
        self.max_microdollars = max_microdollars
        super().__init__(
            f"Model {model!r} has no list price, so API key cost_usd {limit_window} cap "
            f"(max {max_microdollars / 1_000_000:.6f} USD) cannot meter it; request refused"
        )


@dataclass(frozen=True, slots=True)
class ApiKeyRequestUsageBudget:
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        _ensure_optional_int_token_budget("input_tokens", self.input_tokens)
        _ensure_optional_int_token_budget("output_tokens", self.output_tokens)


@dataclass(frozen=True, slots=True)
class LimitRuleData:
    id: int
    limit_type: str
    limit_window: str
    max_value: int
    current_value: int
    model_filter: str | None
    reset_at: datetime


@dataclass(frozen=True, slots=True)
class LimitRuleInput:
    limit_type: str
    limit_window: str
    max_value: int
    model_filter: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyCreateData:
    name: str
    allowed_models: list[str] | None
    apply_to_codex_model: bool = False
    enforced_model: str | None = None
    enforced_reasoning_effort: str | None = None
    enforced_service_tier: str | None = None
    traffic_class: str = TRAFFIC_CLASS_FOREGROUND
    expires_at: datetime | None = None
    assigned_account_ids: list[str] | None = None
    limits: list[LimitRuleInput] = field(default_factory=list)
    member_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyUpdateData:
    name: str | None = None
    name_set: bool = False
    allowed_models: list[str] | None = None
    allowed_models_set: bool = False
    apply_to_codex_model: bool | None = None
    apply_to_codex_model_set: bool = False
    enforced_model: str | None = None
    enforced_model_set: bool = False
    enforced_reasoning_effort: str | None = None
    enforced_reasoning_effort_set: bool = False
    enforced_service_tier: str | None = None
    enforced_service_tier_set: bool = False
    traffic_class: str | None = None
    traffic_class_set: bool = False
    expires_at: datetime | None = None
    expires_at_set: bool = False
    is_active: bool | None = None
    is_active_set: bool = False
    assigned_account_ids: list[str] | None = None
    assigned_account_ids_set: bool = False
    limits: list[LimitRuleInput] | None = None
    limits_set: bool = False
    reset_usage: bool = False
    member_id: str | None = None
    member_id_set: bool = False


@dataclass(frozen=True, slots=True)
class ApiKeyData:
    id: str
    name: str
    key_prefix: str
    allowed_models: list[str] | None
    enforced_model: str | None
    enforced_reasoning_effort: str | None
    enforced_service_tier: str | None
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    apply_to_codex_model: bool = False
    traffic_class: str = TRAFFIC_CLASS_FOREGROUND
    limits: list[LimitRuleData] = field(default_factory=list)
    usage_summary: "ApiKeyUsageSummaryData | None" = None
    account_assignment_scope_enabled: bool = False
    assigned_account_ids: list[str] = field(default_factory=list)
    pooled_credits: "PooledCreditData | None" = None
    member_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyCreatedData(ApiKeyData):
    key: str = ""


@dataclass(frozen=True, slots=True)
class ApiKeyUsageSummaryData:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


@dataclass(frozen=True, slots=True)
class PooledCreditData:
    remaining_percent_primary: float | None = None
    remaining_percent_secondary: float | None = None
    capacity_credits_primary: float = 0.0


@dataclass(frozen=True, slots=True)
class ApiKeyUsageReservationData:
    reservation_id: str
    key_id: str
    model: str


@dataclass(frozen=True, slots=True)
class ApiKeyTrendsPoint:
    t: datetime
    v: float


@dataclass(frozen=True, slots=True)
class ApiKeyTrendsData:
    key_id: str
    cost: list[ApiKeyTrendsPoint] = field(default_factory=list)
    tokens: list[ApiKeyTrendsPoint] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeyAccountCostData:
    account_id: str | None
    email: str | None
    cost_usd: float
    is_deleted: bool = False


@dataclass(frozen=True, slots=True)
class ApiKeyUsage7DayData:
    key_id: str
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_requests: int = 0
    cached_input_tokens: int = 0
    account_costs: list[ApiKeyAccountCostData] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeySelfLimitData:
    limit_type: str
    limit_window: str
    max_value: int
    current_value: int
    remaining_value: int
    model_filter: str | None
    reset_at: datetime
    source: str = "api_key_limit"


@dataclass(frozen=True, slots=True)
class ApiKeySelfUsageData:
    request_count: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    total_cost_usd: float = 0.0
    limits: list[ApiKeySelfLimitData] = field(default_factory=list)


def _ensure_optional_int_token_budget(field_name: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int or None")
