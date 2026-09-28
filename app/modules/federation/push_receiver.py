from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers.registry import list_provider_names
from app.modules.federation.exceptions import FederationConflictError
from app.modules.federation.push_auth import PushSource
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import FederationPushRequest, FederationPushResponse, FederationPushSkip
from app.modules.proxy.account_cache import get_account_selection_cache


def load_binding(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
            raise ValueError("Invalid federation push bindings")
        return data
    except FileNotFoundError:
        return {}


def save_binding(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".federation-push-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _representable_expiry(expires_at_ms: int) -> bool:
    try:
        datetime.fromtimestamp(expires_at_ms / 1000, UTC)
    except (ValueError, OverflowError):
        return False
    return True


class FederationPushReceiver:
    def __init__(
        self, repository: FederationRepository, *, settings: Settings | None = None,
        encryptor: TokenEncryptor | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings or get_settings()
        self._encryptor = encryptor or TokenEncryptor()

    async def receive(self, source: PushSource, request: FederationPushRequest) -> FederationPushResponse:
        owner = request.instance_id
        path = self._settings.federation_push_sources_path.with_name("federation-push-state.json")
        bindings = load_binding(path)
        if owner == self._settings.local_instance_id or bindings.get(source.name, owner) != owner:
            raise FederationConflictError(owner, current_owner=bindings.get(source.name))
        if any(name != source.name and bound == owner for name, bound in bindings.items()):
            raise FederationConflictError(owner, current_owner=owner)
        if source.name not in bindings:
            if await self._repository.has_owner_accounts(owner):
                raise FederationConflictError(owner, current_owner=owner)
            bindings[source.name] = owner
            save_binding(path, bindings)

        accepted: list[str] = []
        skipped: list[FederationPushSkip] = []
        providers = set(list_provider_names())
        now_ms = int(time.time() * 1000)
        for account in request.accounts:
            existing = await self._repository.get_account(account.account_id)
            if existing is not None and existing.owner_instance != owner:
                reason = "conflict_owned_elsewhere"
            elif account.provider not in providers:
                reason = "unsupported_provider"
            elif account.expires_at_ms is not None and (
                account.expires_at_ms <= now_ms or not _representable_expiry(account.expires_at_ms)
            ):
                # Unrepresentable expiries cannot be persisted; skip instead of
                # aborting the push before omitted accounts are deactivated.
                reason = "expired_token"
            elif len(accepted) >= source.max_accounts:
                reason = "over_limit"
            else:
                changed = await self._repository.upsert_mirror_account(
                    account_id=account.account_id, provider=account.provider, email=account.email,
                    alias=account.alias, status=account.status, plan_type=account.plan_type,
                    chatgpt_account_id=account.chatgpt_account_id, access_token=account.access_token,
                    expires_at_ms=account.expires_at_ms, owner_instance_id=owner,
                    local_instance_id=self._settings.local_instance_id, encryptor=self._encryptor,
                )
                if changed:
                    accepted.append(account.account_id)
                    continue
                reason = "conflict_owned_elsewhere"
            skipped.append(FederationPushSkip(account_id=account.account_id, reason=reason))
        removed = await self._repository.deactivate_mirrored_accounts_not_in(
            owner_instance_id=owner, keep_ids={account.account_id for account in request.accounts},
            encryptor=self._encryptor,
        )
        if accepted or removed:
            get_account_selection_cache().invalidate()
        usage = await self._repository.list_local_usage_rollups(
            window_days=self._settings.federation_usage_window_days, account_ids=set(accepted),
        )
        return FederationPushResponse(
            source=source.name, accepted=accepted, skipped=skipped, removed=removed, usage=usage
        )
