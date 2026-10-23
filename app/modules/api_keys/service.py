from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.utils.time import utcnow
from app.db.models import LimitType, LimitWindow
from app.db.request_models import ApiKey, ApiKeyLimit
from app.modules.api_keys.key_validation import (
    _ensure_valid_api_key_row,
    _generate_plain_key,
    _hash_key,
    _lazy_reset_expired_limits,
    _limit_input_to_row,
    _normalize_allowed_models,
    _normalize_expires_at,
    _normalize_model_slug,
    _normalize_name,
    _normalize_reasoning_effort,
    _normalize_service_tier,
    _normalize_traffic_class,
    _serialize_allowed_models,
    _to_api_key_data,
    _to_created_data,
    _to_usage_summary_data,
    _validate_model_enforcement,
)
from app.modules.api_keys.last_used_coalescer import ApiKeyLastUsedCoalescer, get_api_key_last_used_coalescer
from app.modules.api_keys.limit_update_helpers import (
    _REASONING_POLICY_EXCLUSIVE_CONSTRAINT as _REASONING_POLICY_EXCLUSIVE_CONSTRAINT,
)
from app.modules.api_keys.limit_update_helpers import LimitRuleInput as LimitRuleInput
from app.modules.api_keys.limit_update_helpers import (
    _is_reasoning_policy_constraint_error as _is_reasoning_policy_constraint_error,
)
from app.modules.api_keys.limit_update_helpers import _limit_identity_from_input as _limit_identity_from_input
from app.modules.api_keys.limit_update_helpers import _normalize_assigned_source_ids as _normalize_assigned_source_ids
from app.modules.api_keys.limit_update_helpers import (
    _normalize_reasoning_effort_lenient as _normalize_reasoning_effort_lenient,
)
from app.modules.api_keys.limit_update_helpers import (
    _validate_unique_limit_rule_identities as _validate_unique_limit_rule_identities,
)
from app.modules.api_keys.limit_windows import limit_window_delta, next_limit_reset
from app.modules.api_keys.policy_updates import ApiKeyPolicyUpdates as ApiKeyPolicyUpdates
from app.modules.api_keys.policy_validation import (
    _normalize_allowed_reasoning_efforts,
    _normalize_transport_policy_override,
    _normalize_usage_sections,
    _serialize_allowed_reasoning_efforts,
)
from app.modules.api_keys.repository_protocol import ApiKeysRepositoryProtocol
from app.modules.api_keys.reservation_service import ApiKeyReservationService
from app.modules.api_keys.service_data import ApiKeyCreateData as ApiKeyCreateData
from app.modules.api_keys.service_data import (
    ApiKeyCreatedData,
    ApiKeyValidationError,
    MemberKeyScopeRequiredError,
    PooledCreditData,
)
from app.modules.api_keys.service_data import ApiKeyData as ApiKeyData
from app.modules.api_keys.service_data import ApiKeyInvalidError as ApiKeyInvalidError
from app.modules.api_keys.service_data import ApiKeyNotFoundError as ApiKeyNotFoundError
from app.modules.api_keys.service_data import (
    ApiKeyRateLimitExceededError as ApiKeyRateLimitExceededError,
)
from app.modules.api_keys.service_data import (
    ApiKeyRequestUsageBudget as ApiKeyRequestUsageBudget,
)
from app.modules.api_keys.service_data import ApiKeyUsageReservationData as ApiKeyUsageReservationData
from app.modules.api_keys.usage_calculations import _compute_pooled_credits as _compute_pooled_credits
from app.modules.api_keys.usage_service import ApiKeyUsageService
from app.modules.usage.repository import UsageRepository


class ApiKeysService(ApiKeyPolicyUpdates, ApiKeyReservationService, ApiKeyUsageService):
    def __init__(
        self,
        repository: ApiKeysRepositoryProtocol,
        usage_repository: UsageRepository | None = None,
        *,
        last_used_coalescer: ApiKeyLastUsedCoalescer | None = None,
    ) -> None:
        self._repository = repository
        self._usage_repository = usage_repository
        self._last_used_coalescer = last_used_coalescer or get_api_key_last_used_coalescer()

    async def create_key(self, payload: ApiKeyCreateData) -> ApiKeyCreatedData:
        _validate_unique_limit_rule_identities(payload.limits)
        now = utcnow()
        expires_at = _normalize_expires_at(payload.expires_at)
        plain_key = _generate_plain_key()
        normalized_allowed_models = _normalize_allowed_models(payload.allowed_models)
        assigned_account_ids = await self._resolve_assigned_account_ids(payload.assigned_account_ids)
        assigned_source_ids = await self._resolve_assigned_source_ids(payload.assigned_source_ids)
        if payload.member_id is not None and (not assigned_account_ids):
            raise MemberKeyScopeRequiredError("A team member key needs at least one assigned account")
        enforced_model = _normalize_model_slug(payload.enforced_model)
        enforced_reasoning_effort = _normalize_reasoning_effort(payload.enforced_reasoning_effort)
        allowed_reasoning_efforts = _normalize_allowed_reasoning_efforts(payload.allowed_reasoning_efforts)
        enforced_service_tier = _normalize_service_tier(payload.enforced_service_tier)
        traffic_class = _normalize_traffic_class(payload.traffic_class)
        transport_policy_override = _normalize_transport_policy_override(payload.transport_policy_override)
        usage_sections = _normalize_usage_sections(payload.usage_sections)
        _validate_model_enforcement(enforced_model=enforced_model, allowed_models=normalized_allowed_models)
        row = ApiKey(
            id=str(__import__("uuid").uuid4()),
            name=_normalize_name(payload.name),
            key_hash=_hash_key(plain_key),
            key_prefix=plain_key[:15],
            allowed_models=_serialize_allowed_models(normalized_allowed_models),
            apply_to_codex_model=bool(payload.apply_to_codex_model),
            enforced_model=enforced_model,
            enforced_reasoning_effort=enforced_reasoning_effort,
            enforced_service_tier=enforced_service_tier,
            account_assignment_scope_enabled=bool(assigned_account_ids),
            traffic_class=traffic_class,
            expires_at=expires_at,
            is_active=True,
            created_at=now,
            last_used_at=None,
            member_id=payload.member_id,
            allowed_reasoning_efforts=_serialize_allowed_reasoning_efforts(allowed_reasoning_efforts),
            source_assignment_scope_enabled=bool(assigned_source_ids),
            transport_policy_override=transport_policy_override,
            usage_sections=usage_sections,
        )
        try:
            created = await self._repository.create(row, commit=False)
            if assigned_account_ids:
                await self._repository.replace_account_assignments(created.id, assigned_account_ids, commit=False)
            if assigned_source_ids:
                await self._repository.replace_source_assignments(created.id, assigned_source_ids, commit=False)
            if payload.limits:
                limit_rows = [_limit_input_to_row(li, created.id, now) for li in payload.limits]
                await self._repository.upsert_limits(created.id, limit_rows, commit=False)
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            if isinstance(exc, IntegrityError) and _is_reasoning_policy_constraint_error(exc):
                raise ApiKeyValidationError(
                    "enforced_reasoning_effort and allowed_reasoning_efforts cannot be configured together"
                ) from exc
            raise
        created = await self._repository.get_by_id(created.id)
        if created is None:
            raise ValueError("Failed to create API key")
        return _to_created_data(_to_api_key_data(created), plain_key)

    async def list_keys(self) -> list[ApiKeyData]:
        rows = await self._repository.list_all()
        usage_summary_by_key = await self._repository.list_usage_summary_by_key()
        pooled_by_key: dict[str, PooledCreditData] = {}
        if self._usage_repository is not None:
            assigned_ids_by_key = {
                row.id: [a.account_id for a in getattr(row, "account_assignments", [])] for row in rows
            }
            needs_all_accounts = any(
                (not assigned_ids_by_key[row.id] and (not row.account_assignment_scope_enabled) for row in rows)
            )
            if needs_all_accounts:
                all_accounts = await self._repository.list_all_accounts()
                primary_usage = await self._usage_repository.latest_by_account("primary")
                secondary_usage = await self._usage_repository.latest_by_account("secondary")
            else:
                all_account_ids = sorted({account_id for ids in assigned_ids_by_key.values() for account_id in ids})
                if all_account_ids:
                    all_accounts = await self._repository.list_accounts_by_ids(all_account_ids)
                    primary_usage = await self._usage_repository.latest_by_account(
                        "primary", account_ids=all_account_ids
                    )
                    secondary_usage = await self._usage_repository.latest_by_account(
                        "secondary", account_ids=all_account_ids
                    )
                else:
                    all_accounts = []
                    primary_usage = {}
                    secondary_usage = {}
            for row in rows:
                pooled_by_key[row.id] = _compute_pooled_credits(
                    assigned_account_ids=assigned_ids_by_key[row.id],
                    all_accounts=all_accounts,
                    primary_usage=primary_usage,
                    secondary_usage=secondary_usage,
                    account_assignment_scope_enabled=row.account_assignment_scope_enabled,
                )
        return [
            _to_api_key_data(
                row,
                usage_summary=_to_usage_summary_data(usage_summary_by_key.get(row.id)),
                pooled_credits=pooled_by_key.get(row.id),
            )
            for row in rows
        ]

    async def delete_key(self, key_id: str) -> None:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        deleted = await self._repository.delete(key_id)
        if not deleted:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        await get_api_key_cache().invalidate(row.key_hash)
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)

    async def regenerate_key(self, key_id: str) -> ApiKeyCreatedData:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        old_key_hash = row.key_hash
        plain_key = _generate_plain_key()
        updated = await self._repository.update(key_id, key_hash=_hash_key(plain_key), key_prefix=plain_key[:15])
        if updated is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        await get_api_key_cache().invalidate(old_key_hash)
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)
        return _to_created_data(_to_api_key_data(updated), plain_key)

    async def validate_key(self, plain_key: str) -> ApiKeyData:
        if not plain_key:
            raise ApiKeyInvalidError("Missing API key in Authorization header")
        key_hash = _hash_key(plain_key)
        now = utcnow()
        row = _ensure_valid_api_key_row(await self._repository.get_by_hash(key_hash))
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        limits_reset = await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
        refreshed = _ensure_valid_api_key_row(await self._repository.get_by_hash(key_hash)) if limits_reset else row
        if refreshed.expires_at is not None and refreshed.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        return _to_api_key_data(refreshed)

    async def get_key_by_id(self, key_id: str) -> ApiKeyData:
        now = utcnow()
        row = _ensure_valid_api_key_row(await self._repository.get_by_id(key_id))
        if row.expires_at is not None and row.expires_at < now:
            raise ApiKeyInvalidError("API key has expired")
        return _to_api_key_data(row)

    @staticmethod
    def _now() -> datetime:
        return utcnow()


async def _new_limit_input_to_backfilled_row(
    submitted: LimitRuleInput, key_id: str, now: datetime, repository: ApiKeysRepositoryProtocol
) -> ApiKeyLimit:
    limit_type = LimitType(submitted.limit_type)
    window = LimitWindow(submitted.limit_window)
    reset_at = next_limit_reset(now, window)
    since = now - limit_window_delta(window)
    current_value = await repository.get_limit_usage_value(
        key_id, limit_type=limit_type, since=since, until=now, model_filter=submitted.model_filter
    )
    return _limit_input_to_row(submitted, key_id, now, current_value=current_value, reset_at=reset_at)
