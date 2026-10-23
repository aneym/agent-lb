from __future__ import annotations

import time
from fnmatch import fnmatchcase

import anyio

from app.core.openai.model_registry_data import _BOOTSTRAP_AVAILABLE_IN_PLANS as _BOOTSTRAP_AVAILABLE_IN_PLANS
from app.core.openai.model_registry_data import _BOOTSTRAP_CORE_AVAILABLE_IN_PLANS as _BOOTSTRAP_CORE_AVAILABLE_IN_PLANS
from app.core.openai.model_registry_data import _BOOTSTRAP_FAST_SERVICE_TIERS as _BOOTSTRAP_FAST_SERVICE_TIERS
from app.core.openai.model_registry_data import (
    _BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS as _BOOTSTRAP_GPT56_AVAILABLE_IN_PLANS,
)
from app.core.openai.model_registry_data import _BOOTSTRAP_STATIC_MODELS as _BOOTSTRAP_STATIC_MODELS
from app.core.openai.model_registry_data import (
    _BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS as _BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS,
)
from app.core.openai.model_registry_data import _GPT56_SOL_AVAILABILITY_NUX as _GPT56_SOL_AVAILABILITY_NUX
from app.core.openai.model_registry_data import _REASONING_LEVELS_EXTENDED as _REASONING_LEVELS_EXTENDED
from app.core.openai.model_registry_data import _REASONING_LEVELS_MAX as _REASONING_LEVELS_MAX
from app.core.openai.model_registry_data import _REASONING_LEVELS_STANDARD as _REASONING_LEVELS_STANDARD
from app.core.openai.model_registry_data import _REASONING_LEVELS_ULTRA as _REASONING_LEVELS_ULTRA
from app.core.openai.model_registry_data import _SERVICE_TIER_OBJECT_KEY_FIELDS as _SERVICE_TIER_OBJECT_KEY_FIELDS
from app.core.openai.model_registry_data import (
    MODEL_SOURCE_KIND_OPENAI_COMPATIBLE as MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
)
from app.core.openai.model_registry_data import MODEL_SOURCE_KIND_SUBSCRIPTION as MODEL_SOURCE_KIND_SUBSCRIPTION
from app.core.openai.model_registry_data import ModelRegistryExport as ModelRegistryExport
from app.core.openai.model_registry_data import ModelRegistrySnapshot as ModelRegistrySnapshot
from app.core.openai.model_registry_data import ReasoningLevel as ReasoningLevel
from app.core.openai.model_registry_data import UpstreamModel as UpstreamModel
from app.core.openai.model_registry_data import _bootstrap_model as _bootstrap_model
from app.core.openai.model_registry_data import _gpt56_raw as _gpt56_raw
from app.core.openai.model_registry_data import _merge_default_service_tier as _merge_default_service_tier
from app.core.openai.model_registry_data import _merge_service_tier_metadata as _merge_service_tier_metadata
from app.core.openai.model_registry_data import _model_service_tier_keys as _model_service_tier_keys
from app.core.openai.model_registry_data import _service_tier_identity as _service_tier_identity
from app.core.openai.model_registry_data import _union_object_tiers as _union_object_tiers
from app.core.openai.model_registry_data import _union_string_tiers as _union_string_tiers
from app.core.openai.model_registry_data import canonical_service_tier_value as canonical_service_tier_value
from app.core.openai.model_registry_data import is_public_model as is_public_model
from app.core.openai.model_registry_data import logger as logger
from app.core.openai.model_registry_update import ModelRegistryUpdates as ModelRegistryUpdates


class ModelRegistry(ModelRegistryUpdates):
    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._snapshot: ModelRegistrySnapshot | None = None
        self._bootstrap_models: dict[str, UpstreamModel] = {m.slug: m for m in _BOOTSTRAP_STATIC_MODELS}
        self._metadata_models: dict[str, UpstreamModel] | None = None
        self._applied_content_hash: str | None = None
        self._lock = anyio.Lock()

    @property
    def applied_content_hash(self) -> str | None:
        return self._applied_content_hash

    def note_applied_content_hash(self, content_hash: str | None) -> None:
        self._applied_content_hash = content_hash

    async def export_state(self) -> ModelRegistryExport:
        async with self._lock:
            return ModelRegistryExport(snapshot=self._snapshot, metadata_models=self._metadata_models)

    async def import_state(self, state: ModelRegistryExport, *, content_hash: str) -> None:
        async with self._lock:
            self._snapshot = state.snapshot
            self._metadata_models = state.metadata_models
            self._applied_content_hash = content_hash

    def get_snapshot(self) -> ModelRegistrySnapshot | None:
        return self._snapshot

    def get_models_with_fallback(self) -> dict[str, UpstreamModel]:
        snapshot = self._snapshot
        if snapshot is not None:
            if snapshot.bootstrap_floor_active:
                models = dict(self._bootstrap_models)
                for slug in snapshot.suppressed_model_slugs:
                    models.pop(slug, None)
                models.update(snapshot.models)
                return models
            return snapshot.models
        return self._bootstrap_models

    def get_models_for_metadata(self) -> dict[str, UpstreamModel]:
        if self._metadata_models is not None:
            return self._metadata_models
        return self._bootstrap_models

    def plan_types_for_model(self, slug: str) -> frozenset[str] | None:
        normalized_slug = slug.strip().lower()
        bootstrap_model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
        if self._snapshot is None:
            return bootstrap_model.available_in_plans if bootstrap_model is not None else None
        if slug in self._snapshot.model_plans:
            return self._snapshot.model_plans[slug]
        if normalized_slug in self._snapshot.model_plans:
            return self._snapshot.model_plans[normalized_slug]
        if (
            self._snapshot.bootstrap_floor_active
            and bootstrap_model is not None
            and (normalized_slug not in self._snapshot.suppressed_model_slugs)
        ):
            return bootstrap_model.available_in_plans
        snapshot_plans = frozenset()
        return snapshot_plans

    def is_suppressed_model(self, slug: str) -> bool:
        snapshot = self._snapshot
        if snapshot is None:
            return False
        return slug.strip().lower() in snapshot.suppressed_model_slugs

    def plan_types_for_model_service_tier(self, slug: str, service_tier: str | None) -> frozenset[str] | None:
        if service_tier is None:
            return self.plan_types_for_model(slug)
        normalized_slug = slug.strip().lower()
        normalized_service_tier = canonical_service_tier_value(service_tier)
        if not normalized_slug or not normalized_service_tier:
            return self.plan_types_for_model(slug)
        if self._snapshot is None:
            return self.plan_types_for_model(slug)
        if not self._snapshot.account_catalogs_authoritative and self._snapshot.account_plans:
            return self.plan_types_for_model(slug)
        tier_plans = self._snapshot.model_service_tier_plans.get(slug) or self._snapshot.model_service_tier_plans.get(
            normalized_slug
        )
        if tier_plans is None:
            return frozenset() if self._snapshot.account_catalogs_authoritative else self.plan_types_for_model(slug)
        return tier_plans.get(normalized_service_tier, frozenset())

    def account_ids_for_model_service_tier(self, slug: str, service_tier: str | None) -> frozenset[str] | None:
        if service_tier is None or self._snapshot is None:
            return None
        if not self._snapshot.account_catalogs_authoritative:
            return None
        normalized_slug = slug.strip().lower()
        normalized_service_tier = canonical_service_tier_value(service_tier)
        if not normalized_slug or not normalized_service_tier:
            return None
        tier_accounts = self._snapshot.model_service_tier_accounts.get(
            slug
        ) or self._snapshot.model_service_tier_accounts.get(normalized_slug)
        if tier_accounts is None:
            if self._snapshot.account_catalogs_authoritative:
                return frozenset()
            return None
        return tier_accounts.get(normalized_service_tier, frozenset())

    def model_advertises_service_tier(self, slug: str, service_tier: str | None) -> bool:
        if service_tier is None or self._snapshot is None:
            return True
        if not self._snapshot.account_catalogs_authoritative:
            return True
        normalized_slug = slug.strip().lower()
        normalized_service_tier = canonical_service_tier_value(service_tier)
        if not normalized_slug or not normalized_service_tier:
            return True
        model_is_known = (
            slug in self._snapshot.model_plans
            or normalized_slug in self._snapshot.model_plans
            or slug in self._snapshot.model_accounts
            or (normalized_slug in self._snapshot.model_accounts)
        )
        if not model_is_known:
            return True
        tier_accounts = self._snapshot.model_service_tier_accounts.get(
            slug
        ) or self._snapshot.model_service_tier_accounts.get(normalized_slug)
        tier_plans = self._snapshot.model_service_tier_plans.get(slug) or self._snapshot.model_service_tier_plans.get(
            normalized_slug
        )
        if tier_accounts is None and tier_plans is None:
            return False
        return normalized_service_tier in (tier_accounts or {}) or normalized_service_tier in (tier_plans or {})

    def account_ids_for_model(self, slug: str) -> frozenset[str] | None:
        if self._snapshot is None or not self._snapshot.account_catalogs_authoritative:
            return None
        normalized_slug = slug.strip().lower()
        if not normalized_slug:
            return None
        return self._snapshot.model_accounts.get(slug) or self._snapshot.model_accounts.get(
            normalized_slug, frozenset()
        )

    def prefers_websockets(self, slug: str | None) -> bool:
        if not isinstance(slug, str):
            return False
        normalized_slug = slug.strip().lower()
        if not normalized_slug:
            return False
        if self._snapshot is not None:
            model = self._snapshot.models.get(slug) or self._snapshot.models.get(normalized_slug)
            if model is not None:
                return model.prefer_websockets
            if self._snapshot.bootstrap_floor_active and normalized_slug not in self._snapshot.suppressed_model_slugs:
                bootstrap_model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
                if bootstrap_model is not None:
                    return bootstrap_model.prefer_websockets
            return False
        bootstrap_model = self._bootstrap_models.get(slug) or self._bootstrap_models.get(normalized_slug)
        if bootstrap_model is not None:
            return bootstrap_model.prefer_websockets
        return any((fnmatchcase(normalized_slug, pattern) for pattern in _BOOTSTRAP_WEBSOCKET_PREFERRED_MODEL_PATTERNS))

    def needs_refresh(self) -> bool:
        if self._snapshot is None:
            return True
        return time.monotonic() - self._snapshot.fetched_at >= self._ttl_seconds

    async def clear(self) -> None:
        async with self._lock:
            self._snapshot = None
            self._metadata_models = None
            self._applied_content_hash = None


_model_registry = ModelRegistry()


def get_model_registry() -> ModelRegistry:
    return _model_registry
