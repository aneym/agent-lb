from __future__ import annotations

from app.db.session import get_background_session
from app.modules.api_keys.service import ApiKeyData
from app.modules.team.repository import TeamRepository
from app.modules.team.service import TeamMemberSelfStatus, TeamService


async def check_member_gate(api_key: ApiKeyData | None, *, model: str | None) -> None:
    """Apply the team-member gate for a proxied request.

    No-ops when the request carries no API key, or when the key is not attached
    to a team member, so keyless and pre-team-mode deployments never take the
    extra database round trip.
    """

    if api_key is None or getattr(api_key, "member_id", None) is None:
        return

    async with get_background_session() as session:
        await TeamService(TeamRepository(session)).check_member_gate(api_key, model)


async def get_member_self_status(api_key: ApiKeyData | None) -> TeamMemberSelfStatus | None:
    """Member caps/usage/gate for the key's own member, or None when it has no member."""

    member_id = None if api_key is None else getattr(api_key, "member_id", None)
    if member_id is None:
        return None

    async with get_background_session() as session:
        return await TeamService(TeamRepository(session)).get_member_self_status(member_id)
