from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from app.core.auth.refresh import RefreshError, classify_refresh_error
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account
from app.modules.accounts.auth_manager import AccountNotOwnedError, AuthManager, is_locally_owned
from app.modules.federation.scheduler import build_federation_mirror_scheduler
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger(__name__)


class MirrorTokenRefresh:
    _repo_factory: ProxyRepoFactory
    _encryptor: TokenEncryptor

    async def _fresh_access_token(self, account: Account, *, rejected_access_token: str | None = None) -> str:
        @asynccontextmanager
        async def refresh_repo_factory():
            async with self._repo_factory() as repos:
                yield repos.accounts

        try:
            async with self._repo_factory() as repos:
                latest = await repos.accounts.get_by_id(account.id)
                if latest is None:
                    raise AnthropicProxyError(503, "Selected Anthropic account no longer exists")
                manager = AuthManager(repos.accounts, refresh_repo_factory=refresh_repo_factory)
                force = (
                    rejected_access_token is not None
                    and self._encryptor.decrypt(latest.access_token_encrypted) == rejected_access_token
                )
                fresh = await manager.ensure_fresh(latest, force=force)
                return self._encryptor.decrypt(fresh.access_token_encrypted)
        except AccountNotOwnedError as exc:
            reason = str(exc)
            if get_settings().federation_peer_url:
                try:
                    await build_federation_mirror_scheduler().mirror_once()
                    async with self._repo_factory() as repos:
                        latest = await repos.accounts.reload_by_id(account.id)
                        if latest is not None and not is_locally_owned(latest, get_settings()):
                            updated = self._encryptor.decrypt(latest.access_token_encrypted)
                            if updated != rejected_access_token:
                                refreshed = await AuthManager(repos.accounts).ensure_fresh(latest)
                                return self._encryptor.decrypt(refreshed.access_token_encrypted)
                    reason = "Mirror pull returned no usable replacement token"
                except Exception as pull_error:
                    reason = str(pull_error)
            logger.warning(
                "mirror_token_unavailable account_id=%s peer=%s reason=%s",
                account.id,
                get_settings().federation_peer_url,
                reason,
            )
            raise AnthropicProxyError(
                503, "Mirror access token unavailable after pull", code="mirror_token_unavailable"
            ) from exc
        except RefreshError as exc:
            if exc.code == "exchange_uncertain":
                raise AnthropicProxyError(503, exc.message, code=exc.code) from exc

            if exc.is_permanent or classify_refresh_error(exc.code):
                get_account_selection_cache().invalidate()
            raise AnthropicProxyError(401, exc.message, code=exc.code) from exc
