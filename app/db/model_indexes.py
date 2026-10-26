from __future__ import annotations

from sqlalchemy import Index, func, literal_column

from app.db.access_models import ApiKeyUsageReservation, ApiKeyUsageReservationItem, RateLimitAttempt
from app.db.models import Account, AdditionalUsageHistory, UsageHistory
from app.db.quota_models import QuotaPlannerDecision, QuotaWindowObservation
from app.db.request_models import AccountLimitWarmup, ApiKey, ApiKeyAccountAssignment, ApiKeyLimit, RequestLog
from app.db.session_models import HttpBridgeSessionAlias, HttpBridgeSessionRecord, StickySession

_PRIMARY_WINDOW_INDEX_EXPR = func.coalesce(UsageHistory.window, literal_column("'primary'"))


Index("idx_usage_recorded_at", UsageHistory.recorded_at)


Index("idx_usage_account_time", UsageHistory.account_id, UsageHistory.recorded_at)


Index(
    "idx_usage_window_account_time",
    _PRIMARY_WINDOW_INDEX_EXPR,
    UsageHistory.account_id,
    UsageHistory.recorded_at,
)


Index(
    "idx_usage_window_account_latest",
    _PRIMARY_WINDOW_INDEX_EXPR,
    UsageHistory.account_id,
    UsageHistory.recorded_at.desc(),
    UsageHistory.id.desc(),
)


Index(
    "idx_usage_window_raw_account_latest",
    UsageHistory.window,
    UsageHistory.account_id,
    UsageHistory.recorded_at.desc(),
    UsageHistory.id.desc(),
)


Index("idx_accounts_email", Account.email)


Index("idx_api_keys_name", ApiKey.name)


Index("idx_logs_account_time", RequestLog.account_id, RequestLog.requested_at)


Index("idx_logs_api_key_time", RequestLog.api_key_id, RequestLog.requested_at.desc(), RequestLog.id.desc())


Index("idx_logs_api_key_time_account", RequestLog.api_key_id, RequestLog.requested_at.desc(), RequestLog.account_id)


Index("idx_logs_request_kind_time", RequestLog.request_kind, RequestLog.requested_at.desc(), RequestLog.id.desc())


Index("idx_logs_requested_at", RequestLog.requested_at)


Index("idx_logs_source_requested_at", RequestLog.source, RequestLog.requested_at.desc())


Index("idx_logs_session_time", RequestLog.session_id, RequestLog.requested_at.desc())


Index(
    "idx_logs_job_time",
    RequestLog.job_id,
    RequestLog.requested_at.desc(),
    postgresql_where=RequestLog.job_id.is_not(None),
).ddl_if(dialect="postgresql")


Index("idx_logs_requested_at_id", RequestLog.requested_at.desc(), RequestLog.id.desc())


Index(
    "idx_logs_deleted_at_requested_at_id",
    RequestLog.deleted_at,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)


Index(
    "idx_logs_requested_at_model_tier",
    RequestLog.requested_at.desc(),
    RequestLog.model,
    RequestLog.service_tier,
)


Index(
    "idx_logs_model_effort_time",
    RequestLog.model,
    RequestLog.reasoning_effort,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)


Index(
    "idx_logs_status_error_time",
    RequestLog.status,
    RequestLog.error_code,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)


Index(
    "idx_logs_request_status_api_key_time",
    RequestLog.request_id,
    RequestLog.status,
    RequestLog.api_key_id,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)


Index(
    "idx_logs_request_status_api_key_session_time",
    RequestLog.request_id,
    RequestLog.status,
    RequestLog.api_key_id,
    RequestLog.session_id,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)


Index("idx_sticky_account", StickySession.account_id)


Index("idx_sticky_kind_updated_at", StickySession.kind, StickySession.updated_at.desc())


Index("idx_api_keys_hash", ApiKey.key_hash)


Index(
    "idx_account_limit_warmups_account_attempted", AccountLimitWarmup.account_id, AccountLimitWarmup.attempted_at.desc()
)


Index("idx_account_limit_warmups_status_attempted", AccountLimitWarmup.status, AccountLimitWarmup.attempted_at.desc())


Index("idx_api_key_accounts_account_id", ApiKeyAccountAssignment.account_id)


Index("idx_api_key_limits_key_id", ApiKeyLimit.api_key_id)


Index("idx_api_key_limits_reset_at", ApiKeyLimit.reset_at)


Index("idx_api_key_usage_reservations_key_id", ApiKeyUsageReservation.api_key_id)


Index("idx_api_key_usage_reservations_status", ApiKeyUsageReservation.status)


Index(
    "idx_api_key_usage_reservations_status_updated_at", ApiKeyUsageReservation.status, ApiKeyUsageReservation.updated_at
)


Index("idx_api_key_usage_res_items_reservation_id", ApiKeyUsageReservationItem.reservation_id)


Index("idx_quota_planner_decisions_status_created", QuotaPlannerDecision.status, QuotaPlannerDecision.created_at.desc())


Index(
    "idx_quota_planner_decisions_account_created",
    QuotaPlannerDecision.account_id,
    QuotaPlannerDecision.created_at.desc(),
)


Index(
    "idx_quota_window_observations_account_time",
    QuotaWindowObservation.account_id,
    QuotaWindowObservation.observed_at.desc(),
)


Index("idx_http_bridge_sessions_owner_state", HttpBridgeSessionRecord.owner_instance_id, HttpBridgeSessionRecord.state)


Index("idx_http_bridge_sessions_lease", HttpBridgeSessionRecord.lease_expires_at)


Index("idx_http_bridge_sessions_last_seen", HttpBridgeSessionRecord.last_seen_at.desc())


Index(
    "idx_http_bridge_sessions_latest_turn_scope_state_seen",
    HttpBridgeSessionRecord.latest_turn_state,
    HttpBridgeSessionRecord.api_key_scope,
    HttpBridgeSessionRecord.state,
    HttpBridgeSessionRecord.last_seen_at.desc(),
    HttpBridgeSessionRecord.updated_at.desc(),
)


Index(
    "idx_http_bridge_sessions_latest_response_scope_state_seen",
    HttpBridgeSessionRecord.latest_response_id,
    HttpBridgeSessionRecord.api_key_scope,
    HttpBridgeSessionRecord.state,
    HttpBridgeSessionRecord.last_seen_at.desc(),
    HttpBridgeSessionRecord.updated_at.desc(),
)


Index(
    "idx_http_bridge_session_aliases_session_id",
    HttpBridgeSessionAlias.session_id,
)


Index(
    "idx_http_bridge_session_aliases_alias_kind_hash_scope",
    HttpBridgeSessionAlias.alias_kind,
    HttpBridgeSessionAlias.alias_hash,
    HttpBridgeSessionAlias.api_key_scope,
)


Index("ix_additional_usage_history_account_id", AdditionalUsageHistory.account_id)


Index("ix_additional_usage_history_recorded_at", AdditionalUsageHistory.recorded_at)


Index(
    "ix_rate_limit_attempts_type_key_attempted_at",
    RateLimitAttempt.type,
    RateLimitAttempt.key,
    RateLimitAttempt.attempted_at,
)


Index(
    "ix_additional_usage_history_composite",
    AdditionalUsageHistory.account_id,
    AdditionalUsageHistory.quota_key,
    AdditionalUsageHistory.window,
    AdditionalUsageHistory.recorded_at,
)


Index(
    "ix_additional_usage_quota_window",
    AdditionalUsageHistory.quota_key,
    AdditionalUsageHistory.window,
    AdditionalUsageHistory.account_id,
    AdditionalUsageHistory.recorded_at,
)


for _quota_column, _quota_suffix in (
    (AdditionalUsageHistory.quota_key, "key"),
    (func.lower(AdditionalUsageHistory.limit_name), "limit"),
    (func.lower(AdditionalUsageHistory.metered_feature), "feature"),
):
    Index(
        f"ix_additional_latest_{_quota_suffix}",
        AdditionalUsageHistory.account_id,
        AdditionalUsageHistory.window,
        _quota_column,
        AdditionalUsageHistory.recorded_at.desc(),
        AdditionalUsageHistory.used_percent.desc(),
        AdditionalUsageHistory.id.desc(),
    ).ddl_if(dialect="sqlite")


Index(
    "idx_logs_client_session_time",
    RequestLog.client_session_id,
    RequestLog.requested_at.desc(),
    postgresql_where=RequestLog.client_session_id.is_not(None),
).ddl_if(dialect="postgresql")
