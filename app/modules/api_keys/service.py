from __future__ import annotations

from datetime import datetime

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import NAMESPACE_API_KEY, get_cache_invalidation_poller
from app.core.utils.time import utcnow
from app.db.models import ApiKey, ApiKeyLimit
from app.modules.api_keys.key_validation import _build_limit_rows_for_update, _build_reset_limit_rows, _deserialize_allowed_models, _ensure_valid_api_key_row, _generate_plain_key, _hash_key, _lazy_reset_expired_limits, _limit_input_to_row, _normalize_allowed_models, _normalize_assigned_account_ids, _normalize_expires_at, _normalize_model_slug, _normalize_name, _normalize_reasoning_effort, _normalize_service_tier, _normalize_traffic_class, _serialize_allowed_models, _to_api_key_data, _to_created_data, _to_usage_summary_data, _validate_model_enforcement  # noqa: I001  # fmt: skip
from app.modules.api_keys.repository_data import _UNSET, _Unset
from app.modules.api_keys.repository_protocol import ApiKeysRepositoryProtocol
from app.modules.api_keys.reservation_service import ApiKeyReservationService
from app.modules.api_keys.service_data import ApiKeyCreateData, ApiKeyCreatedData, ApiKeyData, ApiKeyInvalidError, ApiKeyNotFoundError, ApiKeyUpdateData, ApiKeyValidationError, MemberKeyScopeRequiredError, PooledCreditData  # noqa: I001  # fmt: skip
from app.modules.api_keys.usage_calculations import _compute_pooled_credits
from app.modules.api_keys.usage_service import ApiKeyUsageService
from app.modules.usage.repository import UsageRepository


class ApiKeysService(ApiKeyReservationService, ApiKeyUsageService):
    def __init__(self, repository: ApiKeysRepositoryProtocol, usage_repository: UsageRepository | None = None) -> None:
        self._repository = repository
        self._usage_repository = usage_repository

    async def create_key(self, payload: ApiKeyCreateData) -> ApiKeyCreatedData:
        now = utcnow()
        expires_at = _normalize_expires_at(payload.expires_at)
        plain_key = _generate_plain_key()
        normalized_allowed_models = _normalize_allowed_models(payload.allowed_models)
        assigned_account_ids = await self._resolve_assigned_account_ids(payload.assigned_account_ids)
        if payload.member_id is not None and not assigned_account_ids:
            raise MemberKeyScopeRequiredError("A team member key needs at least one assigned account")
        enforced_model = _normalize_model_slug(payload.enforced_model)
        enforced_reasoning_effort = _normalize_reasoning_effort(payload.enforced_reasoning_effort)
        enforced_service_tier = _normalize_service_tier(payload.enforced_service_tier)
        traffic_class = _normalize_traffic_class(payload.traffic_class)
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
        )
        try:
            created = await self._repository.create(row, commit=False)

            if assigned_account_ids:
                await self._repository.replace_account_assignments(created.id, assigned_account_ids, commit=False)

            if payload.limits:
                limit_rows = [_limit_input_to_row(li, created.id, now) for li in payload.limits]
                await self._repository.upsert_limits(created.id, limit_rows, commit=False)

            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
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
            needs_all_accounts = any(not assigned_ids for assigned_ids in assigned_ids_by_key.values())
            if needs_all_accounts:
                all_accounts = await self._repository.list_all_accounts()
                primary_usage = await self._usage_repository.latest_by_account("primary")
                secondary_usage = await self._usage_repository.latest_by_account("secondary")
            else:
                all_account_ids = sorted({account_id for ids in assigned_ids_by_key.values() for account_id in ids})
                all_accounts = await self._repository.list_accounts_by_ids(all_account_ids)
                primary_usage = await self._usage_repository.latest_by_account("primary", account_ids=all_account_ids)
                secondary_usage = await self._usage_repository.latest_by_account(
                    "secondary",
                    account_ids=all_account_ids,
                )
            for row in rows:
                pooled_by_key[row.id] = _compute_pooled_credits(
                    assigned_account_ids=assigned_ids_by_key[row.id],
                    all_accounts=all_accounts,
                    primary_usage=primary_usage,
                    secondary_usage=secondary_usage,
                )

        return [
            _to_api_key_data(
                row,
                usage_summary=_to_usage_summary_data(usage_summary_by_key.get(row.id)),
                pooled_credits=pooled_by_key.get(row.id),
            )
            for row in rows
        ]

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
            if existing.member_id is not None and not assigned_account_ids:
                raise MemberKeyScopeRequiredError("A team member key needs at least one assigned account")
            account_assignment_scope_enabled: bool | _Unset = bool(assigned_account_ids)
        else:
            assigned_account_ids = None
            account_assignment_scope_enabled = _UNSET

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

        if payload.enforced_service_tier_set:
            enforced_service_tier = _normalize_service_tier(payload.enforced_service_tier)
        else:
            enforced_service_tier = None

        traffic_class_update: str | _Unset = _UNSET
        if payload.traffic_class_set:
            traffic_class_update = _normalize_traffic_class(payload.traffic_class)

        if payload.allowed_models_set or payload.enforced_model_set:
            effective_allowed_models = (
                allowed_models if payload.allowed_models_set else _deserialize_allowed_models(existing.allowed_models)
            )
            effective_enforced_model = (
                enforced_model if payload.enforced_model_set else _normalize_model_slug(existing.enforced_model)
            )
            _validate_model_enforcement(
                enforced_model=effective_enforced_model,
                allowed_models=effective_allowed_models,
            )

        limit_rows: list[ApiKeyLimit] | None = None
        if payload.limits_set:
            now = utcnow()
            existing_limits = await self._repository.get_limits_by_key(key_id)
            submitted_limits = payload.limits or []
            limit_rows = _build_limit_rows_for_update(
                key_id=key_id,
                now=now,
                submitted_limits=submitted_limits,
                existing_limits=existing_limits,
                reset_usage=payload.reset_usage,
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
                enforced_reasoning_effort=(
                    enforced_reasoning_effort if payload.enforced_reasoning_effort_set else _UNSET
                ),
                enforced_service_tier=(enforced_service_tier if payload.enforced_service_tier_set else _UNSET),
                traffic_class=traffic_class_update,
                account_assignment_scope_enabled=account_assignment_scope_enabled,
                expires_at=expires_at if payload.expires_at_set else _UNSET,
                is_active=(payload.is_active if payload.is_active_set and payload.is_active is not None else _UNSET),
                member_id=payload.member_id if payload.member_id_set else _UNSET,
                commit=False,
            )
            if row is None:
                raise ApiKeyNotFoundError(f"API key not found: {key_id}")

            if payload.assigned_account_ids_set:
                assert assigned_account_ids is not None
                await self._repository.replace_account_assignments(key_id, assigned_account_ids, commit=False)

            if limit_rows is not None:
                await self._repository.upsert_limits(key_id, limit_rows, commit=False)

            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            raise

        if (
            payload.assigned_account_ids_set
            or limit_rows is not None
            or payload.name_set
            or payload.allowed_models_set
            or payload.apply_to_codex_model_set
            or payload.enforced_model_set
            or payload.enforced_reasoning_effort_set
            or payload.enforced_service_tier_set
            or payload.traffic_class_set
            or payload.expires_at_set
            or payload.is_active_set
            or payload.member_id_set
        ):
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
        updated = await self._repository.update(
            key_id,
            key_hash=_hash_key(plain_key),
            key_prefix=plain_key[:15],
        )
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
