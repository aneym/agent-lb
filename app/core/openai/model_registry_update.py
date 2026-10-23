from __future__ import annotations

import time
from collections.abc import Iterable

from app.core.openai.model_registry_data import (
    ModelRegistrySnapshot,
    UpstreamModel,
    _merge_service_tier_metadata,
    _model_service_tier_keys,
    logger,
)


class ModelRegistryUpdates:
    async def update(
        self,
        per_plan_results: dict[str, list[UpstreamModel]],
        *,
        per_account_results: dict[str, tuple[str, list[UpstreamModel]]] | None = None,
        active_account_plans: dict[str, str] | None = None,
    ) -> None:
        if not per_plan_results:
            logger.warning("Model registry refresh produced no plan results; keeping cached snapshot")
            return
        async with self._lock:
            previous = self._snapshot
            try:
                models: dict[str, UpstreamModel] = {}
                model_plans: dict[str, set[str]] = {}
                model_service_tier_plans: dict[str, dict[str, set[str]]] = {}
                model_service_tier_accounts: dict[str, dict[str, set[str]]] = {}
                model_accounts: dict[str, set[str]] = {}
                account_plans: dict[str, str] = {}
                suppressed_model_slugs: set[str] = (
                    set(previous.suppressed_model_slugs) if previous is not None else set()
                )
                if previous is not None:
                    previous_plans = set(previous.plan_models.keys())
                    refreshed_plans = set(per_plan_results.keys())
                    stale_plans = previous_plans - refreshed_plans
                    if active_account_plans is not None:
                        stale_plans.intersection_update(active_account_plans.values())
                        stale_account_ids = {
                            account_id
                            for account_id, plan_type in previous.account_plans.items()
                            if account_id in active_account_plans
                            and plan_type in stale_plans
                            and (active_account_plans[account_id] == plan_type)
                        }
                        refreshed_account_ids = set(per_account_results or {})
                        stale_account_ids.update(
                            (
                                account_id
                                for account_id, previous_plan_type in previous.account_plans.items()
                                if account_id in active_account_plans
                                and account_id not in refreshed_account_ids
                                and (active_account_plans[account_id] == previous_plan_type)
                            )
                        )
                    else:
                        stale_account_ids = {
                            account_id
                            for account_id, plan_type in previous.account_plans.items()
                            if plan_type in stale_plans
                        }
                    supported_stale_slugs: dict[str, set[str]] | None = None
                    if active_account_plans is not None:
                        supported_stale_slugs = {}
                        for slug, account_ids in previous.model_accounts.items():
                            for account_id in account_ids:
                                if account_id not in stale_account_ids:
                                    continue
                                plan_of_account = active_account_plans.get(
                                    account_id, previous.account_plans.get(account_id)
                                )
                                if plan_of_account in stale_plans:
                                    supported_stale_slugs.setdefault(plan_of_account, set()).add(slug)
                    for plan_type in stale_plans:
                        stale_slugs: Iterable[str] = previous.plan_models.get(plan_type, frozenset())
                        if supported_stale_slugs is not None:
                            supported = supported_stale_slugs.get(plan_type, set())
                            stale_slugs = [
                                slug
                                for slug in stale_slugs
                                if not previous.model_accounts.get(slug) or slug in supported
                            ]
                        for slug in stale_slugs:
                            if slug not in models and slug in previous.models:
                                models[slug] = previous.models[slug]
                            model_plans.setdefault(slug, set()).add(plan_type)
                            stale_tier_plans = previous.model_service_tier_plans.get(slug, {})
                            for service_tier, plans in stale_tier_plans.items():
                                if plan_type in plans:
                                    model_service_tier_plans.setdefault(slug, {}).setdefault(service_tier, set()).add(
                                        plan_type
                                    )
                    for account_id in stale_account_ids:
                        plan_type = (
                            active_account_plans.get(account_id)
                            if active_account_plans is not None
                            else previous.account_plans.get(account_id)
                        )
                        if plan_type is not None:
                            account_plans[account_id] = plan_type
                    for slug, account_ids in previous.model_accounts.items():
                        if slug not in previous.models:
                            continue
                        stale_model_accounts = account_ids & stale_account_ids
                        if not stale_model_accounts:
                            continue
                        model_accounts.setdefault(slug, set()).update(stale_model_accounts)
                        if slug in previous.models:
                            models.setdefault(slug, previous.models[slug])
                        for account_id in stale_model_accounts:
                            plan_type = account_plans.get(account_id)
                            if plan_type is not None:
                                model_plans.setdefault(slug, set()).add(plan_type)
                    for slug, tier_accounts in previous.model_service_tier_accounts.items():
                        if slug not in previous.models:
                            continue
                        for service_tier, account_ids in tier_accounts.items():
                            stale_tier_accounts = account_ids & stale_account_ids
                            if stale_tier_accounts:
                                model_service_tier_accounts.setdefault(slug, {}).setdefault(service_tier, set()).update(
                                    stale_tier_accounts
                                )
                                for account_id in stale_tier_accounts:
                                    plan_type = account_plans.get(account_id)
                                    if plan_type is not None:
                                        model_service_tier_plans.setdefault(slug, {}).setdefault(
                                            service_tier, set()
                                        ).add(plan_type)
                refreshed_model_slugs: set[str] = set()
                for plan_type, plan_models_list in per_plan_results.items():
                    for model in plan_models_list:
                        existing = models.get(model.slug)
                        if existing is not None and model.slug in refreshed_model_slugs:
                            models[model.slug] = _merge_service_tier_metadata(existing, model)
                        else:
                            models[model.slug] = model
                        refreshed_model_slugs.add(model.slug)
                        model_plans.setdefault(model.slug, set()).add(plan_type)
                        for service_tier in _model_service_tier_keys(model):
                            model_service_tier_plans.setdefault(model.slug, {}).setdefault(service_tier, set()).add(
                                plan_type
                            )
                if per_account_results is not None:
                    for account_id, (plan_type, account_models) in per_account_results.items():
                        account_plans[account_id] = plan_type
                        for model in account_models:
                            if model.slug not in models:
                                continue
                            model_accounts.setdefault(model.slug, set()).add(account_id)
                            for service_tier in _model_service_tier_keys(model):
                                model_service_tier_accounts.setdefault(model.slug, {}).setdefault(
                                    service_tier, set()
                                ).add(account_id)
                frozen_model_plans: dict[str, frozenset[str]] = {
                    slug: frozenset(plans) for slug, plans in model_plans.items()
                }
                frozen_model_service_tier_plans: dict[str, dict[str, frozenset[str]]] = {
                    slug: {service_tier: frozenset(plans) for service_tier, plans in tier_plans.items()}
                    for slug, tier_plans in model_service_tier_plans.items()
                }
                frozen_model_service_tier_accounts: dict[str, dict[str, frozenset[str]]] = {
                    slug: {service_tier: frozenset(account_ids) for service_tier, account_ids in tier_accounts.items()}
                    for slug, tier_accounts in model_service_tier_accounts.items()
                }
                frozen_model_accounts = {slug: frozenset(account_ids) for slug, account_ids in model_accounts.items()}
                authoritative_account_catalogs = per_account_results is not None and set(
                    active_account_plans or per_account_results
                ).issubset(account_plans)
                if authoritative_account_catalogs:
                    for slug in self._bootstrap_models:
                        if slug not in models:
                            suppressed_model_slugs.add(slug)
                    for _plan_type, account_models in (per_account_results or {}).values():
                        for model in account_models:
                            if model.slug not in models:
                                suppressed_model_slugs.add(model.slug)
                if previous is not None:
                    for slug, previous_account_ids in previous.model_accounts.items():
                        if slug in models or not previous_account_ids:
                            continue
                        suppressed_model_slugs.add(slug)
                for slug in tuple(suppressed_model_slugs):
                    if slug in models:
                        suppressed_model_slugs.discard(slug)
                plan_models_index: dict[str, set[str]] = {}
                for slug, plans in frozen_model_plans.items():
                    for plan_type in plans:
                        plan_models_index.setdefault(plan_type, set()).add(slug)
                frozen_plan_models: dict[str, frozenset[str]] = {
                    plan_type: frozenset(slugs) for plan_type, slugs in plan_models_index.items()
                }
                bootstrap_floor_active = per_account_results is not None and (not authoritative_account_catalogs)
                metadata_models = {
                    slug: model
                    for slug, model in (self._metadata_models or self._bootstrap_models).items()
                    if slug in self._bootstrap_models
                }
                if per_account_results is not None:
                    account_metadata_models: dict[str, UpstreamModel] = {}
                    for _account_id, (_plan_type, account_models) in per_account_results.items():
                        for model in account_models:
                            if model.slug in self._bootstrap_models:
                                account_metadata_models.setdefault(model.slug, model)
                    metadata_models.update(account_metadata_models)
                metadata_models.update(models)
                self._snapshot = ModelRegistrySnapshot(
                    models=models,
                    model_plans=frozen_model_plans,
                    plan_models=frozen_plan_models,
                    model_service_tier_plans=frozen_model_service_tier_plans,
                    model_service_tier_accounts=frozen_model_service_tier_accounts,
                    account_plans=account_plans,
                    fetched_at=time.monotonic(),
                    model_accounts=frozen_model_accounts,
                    account_catalogs_authoritative=authoritative_account_catalogs,
                    bootstrap_floor_active=bootstrap_floor_active,
                    suppressed_model_slugs=frozenset(suppressed_model_slugs),
                )
                self._metadata_models = metadata_models
            except Exception:
                self._snapshot = previous
                logger.warning("Model registry refresh failed; keeping cached snapshot", exc_info=True)
                raise
