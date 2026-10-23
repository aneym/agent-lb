from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.utils.time import utcnow
from app.db.request_models import ApiKeyLimit
from app.modules.api_keys.key_validation import (
    _build_limit_rows_for_update,
    _build_reset_limit_rows,
    _deserialize_allowed_models,
    _normalize_allowed_models,
    _normalize_assigned_account_ids,
    _normalize_expires_at,
    _normalize_model_slug,
    _normalize_name,
    _normalize_reasoning_effort,
    _normalize_service_tier,
    _normalize_traffic_class,
    _serialize_allowed_models,
    _to_api_key_data,
    _validate_model_enforcement,
)
from app.modules.api_keys.limit_update_helpers import (
    _is_reasoning_policy_constraint_error,
    _normalize_assigned_source_ids,
    _normalize_reasoning_effort_lenient,
)
from app.modules.api_keys.policy_validation import (
    _deserialize_allowed_reasoning_efforts,
    _normalize_allowed_reasoning_efforts,
    _normalize_transport_policy_override,
    _normalize_usage_sections,
    _serialize_allowed_reasoning_efforts,
    _validate_reasoning_effort_policy,
)
from app.modules.api_keys.repository_data import _UNSET, _Unset
from app.modules.api_keys.service_data import (
    ApiKeyData,
    ApiKeyNotFoundError,
    ApiKeyUpdateData,
    ApiKeyValidationError,
    MemberKeyScopeRequiredError,
)


class ApiKeyPolicyUpdates:
    async def update_key(self, key_id: str, payload: ApiKeyUpdateData) -> ApiKeyData:
        expires_at = _normalize_expires_at(payload.expires_at) if payload.expires_at_set else None
        existing = await self._repository.get_by_id(key_id)
        if existing is None:
            raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        if payload.allowed_models_set:
            allowed_models = _normalize_allowed_models(payload.allowed_models)
        else:
            allowed_models = None
        if payload.assigned_account_ids_set:
            assigned_account_ids = await self._resolve_assigned_account_ids(payload.assigned_account_ids)
            if existing.member_id is not None and (not assigned_account_ids):
                raise MemberKeyScopeRequiredError("A team member key needs at least one assigned account")
            account_assignment_scope_enabled: bool | _Unset = bool(assigned_account_ids)
        else:
            assigned_account_ids = None
            account_assignment_scope_enabled = _UNSET
        if payload.assigned_source_ids_set:
            assigned_source_ids = await self._resolve_assigned_source_ids(payload.assigned_source_ids)
            source_assignment_scope_enabled: bool | _Unset = bool(assigned_source_ids)
        else:
            assigned_source_ids = None
            source_assignment_scope_enabled = _UNSET
        if payload.enforced_model_set:
            enforced_model = _normalize_model_slug(payload.enforced_model)
        else:
            enforced_model = None
        apply_to_codex_model: bool | _Unset
        if payload.apply_to_codex_model_set:
            if payload.apply_to_codex_model is None:
                apply_to_codex_model = _UNSET
            else:
                apply_to_codex_model = payload.apply_to_codex_model
        else:
            apply_to_codex_model = _UNSET
        if payload.enforced_reasoning_effort_set:
            enforced_reasoning_effort = _normalize_reasoning_effort(payload.enforced_reasoning_effort)
        else:
            enforced_reasoning_effort = None
        if payload.allowed_reasoning_efforts_set:
            allowed_reasoning_efforts = _normalize_allowed_reasoning_efforts(payload.allowed_reasoning_efforts)
        else:
            allowed_reasoning_efforts = None
        if payload.enforced_service_tier_set:
            enforced_service_tier = _normalize_service_tier(payload.enforced_service_tier)
        else:
            enforced_service_tier = None
        traffic_class_update: str | _Unset = _UNSET
        if payload.traffic_class_set:
            traffic_class_update = _normalize_traffic_class(payload.traffic_class)
        transport_policy_override_update: str | None | _Unset = _UNSET
        if payload.transport_policy_override_set:
            transport_policy_override_update = _normalize_transport_policy_override(payload.transport_policy_override)
        usage_sections: str | _Unset = _UNSET
        if payload.usage_sections_set:
            usage_sections = _normalize_usage_sections(payload.usage_sections)
        if payload.allowed_models_set or payload.enforced_model_set:
            effective_allowed_models = (
                allowed_models if payload.allowed_models_set else _deserialize_allowed_models(existing.allowed_models)
            )
            effective_enforced_model = (
                enforced_model if payload.enforced_model_set else _normalize_model_slug(existing.enforced_model)
            )
            _validate_model_enforcement(
                enforced_model=effective_enforced_model, allowed_models=effective_allowed_models
            )
        if payload.enforced_reasoning_effort_set or payload.allowed_reasoning_efforts_set:
            effective_enforced_reasoning_effort = (
                enforced_reasoning_effort
                if payload.enforced_reasoning_effort_set
                else _normalize_reasoning_effort_lenient(existing.enforced_reasoning_effort)
            )
            effective_allowed_reasoning_efforts = (
                allowed_reasoning_efforts
                if payload.allowed_reasoning_efforts_set
                else _deserialize_allowed_reasoning_efforts(existing.allowed_reasoning_efforts)
            )
            _validate_reasoning_effort_policy(
                enforced_reasoning_effort=effective_enforced_reasoning_effort,
                allowed_reasoning_efforts=effective_allowed_reasoning_efforts,
            )
        limit_rows: list[ApiKeyLimit] | None = None
        if payload.limits_set:
            now = utcnow()
            existing_limits = await self._repository.get_limits_by_key(key_id)
            submitted_limits = payload.limits or []
            limit_rows = await _build_limit_rows_for_update(
                key_id=key_id,
                now=now,
                submitted_limits=submitted_limits,
                existing_limits=existing_limits,
                reset_usage=payload.reset_usage,
                repository=self._repository,
            )
        elif payload.reset_usage:
            now = utcnow()
            existing_limits = await self._repository.get_limits_by_key(key_id)
            limit_rows = _build_reset_limit_rows(key_id=key_id, now=now, existing_limits=existing_limits)
        try:
            row = await self._repository.update(
                key_id,
                name=_normalize_name(payload.name or "") if payload.name_set else _UNSET,
                allowed_models=_serialize_allowed_models(allowed_models) if payload.allowed_models_set else _UNSET,
                apply_to_codex_model=apply_to_codex_model,
                enforced_model=enforced_model if payload.enforced_model_set else _UNSET,
                enforced_reasoning_effort=enforced_reasoning_effort
                if payload.enforced_reasoning_effort_set
                else _UNSET,
                enforced_service_tier=enforced_service_tier if payload.enforced_service_tier_set else _UNSET,
                traffic_class=traffic_class_update,
                account_assignment_scope_enabled=account_assignment_scope_enabled,
                expires_at=expires_at if payload.expires_at_set else _UNSET,
                is_active=payload.is_active if payload.is_active_set and payload.is_active is not None else _UNSET,
                member_id=payload.member_id if payload.member_id_set else _UNSET,
                commit=False,
                allowed_reasoning_efforts=_serialize_allowed_reasoning_efforts(allowed_reasoning_efforts)
                if payload.allowed_reasoning_efforts_set
                else _UNSET,
                transport_policy_override=transport_policy_override_update,
                usage_sections=usage_sections,
                source_assignment_scope_enabled=source_assignment_scope_enabled,
            )
            if row is None:
                raise ApiKeyNotFoundError(f"API key not found: {key_id}")
            if payload.assigned_account_ids_set:
                assert assigned_account_ids is not None
                await self._repository.replace_account_assignments(key_id, assigned_account_ids, commit=False)
            if payload.assigned_source_ids_set:
                assert assigned_source_ids is not None
                await self._repository.replace_source_assignments(key_id, assigned_source_ids, commit=False)
            if limit_rows is not None:
                await self._repository.upsert_limits(key_id, limit_rows, commit=False)
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            if isinstance(exc, IntegrityError) and _is_reasoning_policy_constraint_error(exc):
                raise ApiKeyValidationError(
                    "enforced_reasoning_effort and allowed_reasoning_efforts cannot be configured together"
                ) from exc
            raise
        if True:
            row = await self._repository.get_by_id(key_id)
            if row is None:
                raise ApiKeyNotFoundError(f"API key not found: {key_id}")
        await get_api_key_cache().invalidate(row.key_hash)
        poller = get_cache_invalidation_poller()
        if poller is not None:
            await poller.bump(NAMESPACE_API_KEY)
        return _to_api_key_data(row)

    async def _resolve_assigned_account_ids(self, account_ids: list[str] | None) -> list[str]:
        normalized_account_ids = _normalize_assigned_account_ids(account_ids)
        existing_accounts = await self._repository.list_accounts_by_ids(normalized_account_ids)
        existing_account_ids = {account.id for account in existing_accounts}
        missing_account_ids = [
            account_id for account_id in normalized_account_ids if account_id not in existing_account_ids
        ]
        if missing_account_ids:
            missing = ", ".join(missing_account_ids)
            raise ApiKeyValidationError(f"Unknown account ids: {missing}")
        return normalized_account_ids

    async def _resolve_assigned_source_ids(self, source_ids: list[str] | None) -> list[str]:
        normalized_source_ids = _normalize_assigned_source_ids(source_ids)
        if not normalized_source_ids:
            return []
        existing_sources = await self._repository.list_model_sources_by_ids(normalized_source_ids)
        existing_source_ids = {source.id for source in existing_sources}
        missing_source_ids = [source_id for source_id in normalized_source_ids if source_id not in existing_source_ids]
        if missing_source_ids:
            missing = ", ".join(missing_source_ids)
            raise ApiKeyValidationError(f"Unknown model source ids: {missing}")
        return normalized_source_ids
