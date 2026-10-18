from __future__ import annotations
import asyncio
from app.core.auth.refresh import RefreshError, TokenRefreshResult


class _RotatingTokenEndpoint:
    def __init__(self) -> None:
        self.presented: list[str] = []
        self._spent: set[str] = set()

    async def refresh(self, refresh_token: str) -> TokenRefreshResult:
        self.presented.append(refresh_token)
        if refresh_token in self._spent:
            raise RefreshError("refresh_token_reused", "Refresh token was reused", True)
        self._spent.add(refresh_token)
        await asyncio.sleep(0.5)
        n = len(self._spent)
        return TokenRefreshResult(
            access_token=f"access-{n}",
            refresh_token=f"refresh-{n}",
            id_token=f"id-{n}",
            account_id=None,
            plan_type=None,
            email=None,
        )
