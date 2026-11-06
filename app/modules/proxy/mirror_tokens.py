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
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger(__name__)

MIRROR_TOKEN_REVOKED_COOLDOWN_SECONDS = 60
MIRROR_TOKEN_UNAVAILABLE_COOLDOWN_SECONDS = 15


class MirrorTokenRefresh:
    _repo_factory: ProxyRepoFactory
    _encryptor: TokenEncryptor
    _load_balancer: LoadBalancer

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
            return await self._pull_mirror_token(account, rejected_access_token, exc)
        except RefreshError as exc:
            if exc.code == "exchange_uncertain":
                raise AnthropicProxyError(503, exc.message, code=exc.code) from exc

            if exc.is_permanent or classify_refresh_error(exc.code):
                get_account_selection_cache().invalidate()
            raise AnthropicProxyError(401, exc.message, code=exc.code) from exc

    async def _pull_mirror_token(
        self, account: Account, rejected_access_token: str | None, not_owned: AccountNotOwnedError
    ) -> str:
        settings = get_settings()
        reason = "federation peer is not configured"
        if settings.federation_peer_url:
            try:
                await build_federation_mirror_scheduler().mirror_once()
                async with self._repo_factory() as repos:
                    latest = await repos.accounts.reload_by_id(account.id)
                    if latest is None or is_locally_owned(latest, settings):
                        reason = "mirror pull returned no mirrored account"
                    elif self._encryptor.decrypt(latest.access_token_encrypted) == rejected_access_token:
                        await self._load_balancer.cool_down(
                            account, MIRROR_TOKEN_REVOKED_COOLDOWN_SECONDS, error_rule=True
                        )
                        logger.warning("mirror_token_revoked account_id=%s", account.id)
                        raise AnthropicProxyError(
                            401, "Mirrored account token was rejected upstream", code="mirror_token_revoked"
                        ) from not_owned
                    else:
                        refreshed = await AuthManager(repos.accounts).ensure_fresh(latest)
                        return self._encryptor.decrypt(refreshed.access_token_encrypted)
            except AnthropicProxyError:
                raise
            except AccountNotOwnedError:
                reason = "mirror pull returned an expired token"
            except Exception as pull_error:
                reason = f"mirror pull failed error_type={type(pull_error).__name__}"
        await self._load_balancer.cool_down(account, MIRROR_TOKEN_UNAVAILABLE_COOLDOWN_SECONDS, error_rule=True)
        logger.warning("mirror_token_unavailable account_id=%s reason=%s", account.id, reason)
        raise AnthropicProxyError(
            503, "Mirror access token unavailable after pull", code="mirror_token_unavailable"
        ) from not_owned
