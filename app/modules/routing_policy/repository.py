from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RoutingPolicyVersion


class RoutingPolicyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def active(self) -> RoutingPolicyVersion | None:
        return await self.session.scalar(
            select(RoutingPolicyVersion)
            .where(RoutingPolicyVersion.state == "active")
            .order_by(RoutingPolicyVersion.version.desc())
        )

    async def next_version(self) -> int:
        return (await self.session.scalar(select(func.max(RoutingPolicyVersion.version))) or 0) + 1

    async def all(self) -> list[RoutingPolicyVersion]:
        return list(
            (
                await self.session.scalars(select(RoutingPolicyVersion).order_by(RoutingPolicyVersion.version.desc()))
            ).all()
        )

    async def get(self, version: int) -> RoutingPolicyVersion | None:
        return await self.session.scalar(select(RoutingPolicyVersion).where(RoutingPolicyVersion.version == version))
