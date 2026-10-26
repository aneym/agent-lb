from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from app.db.models import Account
from app.modules.accounts.auth_manager import AccountsRepositoryPort, AuthManager, RefreshAdmissionLeasePort
from app.modules.accounts.repository import AccountsRepository


class ReleasingAuthManager(AuthManager):
    def __init__(
        self,
        repo: AccountsRepository,
        *,
        acquire_refresh_admission: Callable[[], Awaitable[RefreshAdmissionLeasePort]] | None = None,
        refresh_repo_factory: Callable[[], AbstractAsyncContextManager[AccountsRepositoryPort]] | None = None,
    ) -> None:
        super().__init__(
            repo,
            acquire_refresh_admission=acquire_refresh_admission,
            refresh_repo_factory=refresh_repo_factory,
        )
        self._request_repo = repo

    async def _run_refresh(self, account: Account) -> Account:
        await self._request_repo.release()
        return await super()._run_refresh(account)
