from __future__ import annotations

from app.db.session import get_background_session
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.team.repository import TeamRepository
from app.modules.team.service import TeamMemberSelfStatus, TeamService


async def check_member_gate(api_key: ApiKeyData | None, *, model: str | None) -> None:

    if api_key is None or getattr(api_key, "member_id", None) is None:
        return

    async with get_background_session() as session:
        await TeamService(TeamRepository(session)).check_member_gate(api_key, model)


async def get_member_self_status(api_key: ApiKeyData | None) -> TeamMemberSelfStatus | None:

    member_id = None if api_key is None else getattr(api_key, "member_id", None)
    if member_id is None:
        return None

    async with get_background_session() as session:
        return await TeamService(TeamRepository(session)).get_member_self_status(member_id)
