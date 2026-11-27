from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from app.core.crypto import TokenEncryptor
from app.db.oauth_models import OAuthFlow
from app.db.session import get_background_session
from app.modules.oauth.state import OAUTH_FLOW_TTL_SECONDS, ExpectedAccount, OAuthState

_EXPIRED = "OAuth flow expired. Start sign-in again."
POST_SAVE_TIMEOUT_SECONDS = 30
_SECRET_FIELDS = (
    "state_token",
    "code_verifier",
    "device_auth_id",
    "user_code",
    "warmup_hours",
    "warmup_capacity_percent",
    "warmup_max_concurrent",
)


def _state_digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


class DurableOAuthFlows:
    def __init__(self, encryptor: TokenEncryptor) -> None:
        self._encryptor = encryptor

    def _decode(self, row: OAuthFlow, *, include_secrets: bool = False) -> OAuthState:
        flow = OAuthState(
            flow_id=row.id,
            method=row.method,
            provider=row.provider,
            status=row.status,
            error_message=row.error_message,
            probe_status_code=row.probe_status_code,
            started_at=row.started_at,
            expires_at=row.expires_at,
            interval_seconds=row.interval_seconds,
            poll_after=row.poll_after,
        )
        if row.status in {"pending", "processing"} and row.expires_at <= time.time():
            flow.status = "error"
            flow.error_message = _EXPIRED
        if row.status == "verifying" and row.verification_deadline is not None:
            if row.verification_deadline <= time.time():
                flow.status = "success"
                flow.probe_status_code = 0
        if include_secrets and flow.status == "pending" and row.secrets_encrypted is not None:
            secrets = json.loads(self._encryptor.decrypt(row.secrets_encrypted))
            for field in _SECRET_FIELDS:
                setattr(flow, field, secrets[field])
            if snapshot := secrets["expected_account"]:
                flow.expected_account = ExpectedAccount(**snapshot)
        return flow

    async def insert(self, flow: OAuthState) -> None:
        assert flow.flow_id and flow.method and flow.started_at is not None and flow.expires_at is not None
        secrets = {field: getattr(flow, field) for field in _SECRET_FIELDS}
        secrets["expected_account"] = asdict(flow.expected_account) if flow.expected_account else None
        async with get_background_session() as session:
            session.add(
                OAuthFlow(
                    id=flow.flow_id,
                    method=flow.method,
                    provider=flow.provider,
                    status="pending",
                    state_digest=_state_digest(flow.state_token) if flow.state_token is not None else None,
                    secrets_encrypted=self._encryptor.encrypt(json.dumps(secrets)),
                    started_at=flow.started_at,
                    expires_at=flow.expires_at,
                    interval_seconds=flow.interval_seconds,
                )
            )
            await session.commit()

    async def get(
        self, flow_id: str | None = None, *, state: str | None = None, include_secrets: bool = False
    ) -> OAuthState | None:
        statement = select(OAuthFlow)
        if flow_id is not None:
            statement = statement.where(OAuthFlow.id == flow_id)
        if state is not None:
            statement = statement.where(OAuthFlow.state_digest == _state_digest(state))
        if flow_id is None and state is None:
            statement = statement.order_by(OAuthFlow.started_at.desc()).limit(1)
        async with get_background_session() as session:
            row = await session.scalar(statement)
            return self._decode(row, include_secrets=include_secrets) if row is not None else None

    async def active(self) -> list[OAuthState]:
        async with get_background_session() as session:
            rows = await session.scalars(
                select(OAuthFlow)
                .where(OAuthFlow.status == "pending", OAuthFlow.expires_at > time.time())
                .order_by(OAuthFlow.started_at)
            )
            return [self._decode(row) for row in rows]

    async def claim(self, flow_id: str, *, poll_owner: str | None = None) -> bool:
        statement = update(OAuthFlow).where(
            OAuthFlow.id == flow_id, OAuthFlow.status == "pending", OAuthFlow.expires_at > time.time()
        )
        if poll_owner is None:
            statement = statement.where(OAuthFlow.method == "browser")
        else:
            statement = statement.where(
                OAuthFlow.method == "device", OAuthFlow.poll_owner == poll_owner, OAuthFlow.poll_after > time.time()
            )
        return await self._write(
            statement.values(status="processing", secrets_encrypted=None, poll_owner=None, poll_after=None)
        )

    async def claimed_device_poll(self, flow_id: str, owner: str, *, until: float) -> OAuthState | None:
        now = time.time()
        async with get_background_session() as session:
            row = (
                await session.scalars(
                    update(OAuthFlow)
                    .where(
                        OAuthFlow.id == flow_id,
                        OAuthFlow.method == "device",
                        OAuthFlow.status == "pending",
                        OAuthFlow.expires_at > now,
                        or_(OAuthFlow.poll_after.is_(None), OAuthFlow.poll_after <= now),
                    )
                    .values(poll_after=until, poll_owner=owner)
                    .returning(OAuthFlow)
                )
            ).one_or_none()
            await session.commit()
            return self._decode(row, include_secrets=True) if row is not None else None

    async def release_device_poll(self, flow_id: str, owner: str, *, poll_after: float | None) -> None:
        await self._write(
            update(OAuthFlow)
            .where(OAuthFlow.id == flow_id, OAuthFlow.status == "pending", OAuthFlow.poll_owner == owner)
            .values(poll_owner=None, poll_after=poll_after)
        )

    async def finish(
        self,
        flow_id: str,
        *,
        from_status: str = "processing",
        error: str | None,
        probe_status_code: int | None = None,
        poll_owner: str | None = None,
    ) -> bool:
        statement = update(OAuthFlow).where(OAuthFlow.id == flow_id, OAuthFlow.status == from_status)
        if poll_owner is not None:
            statement = statement.where(OAuthFlow.poll_owner == poll_owner)
        elif from_status == "pending":
            statement = statement.where(OAuthFlow.method == "browser", OAuthFlow.expires_at > time.time())
        return await self._write(
            statement.values(
                status="error" if error is not None else "success",
                error_message=error,
                probe_status_code=probe_status_code,
                verification_deadline=None,
                secrets_encrypted=None,
                poll_owner=None,
                poll_after=None,
            )
        )

    async def prune(self) -> None:
        now = time.time()
        async with get_background_session() as session:
            await session.execute(delete(OAuthFlow).where(OAuthFlow.expires_at <= now - OAUTH_FLOW_TTL_SECONDS))
            await session.execute(
                update(OAuthFlow)
                .where(OAuthFlow.expires_at <= now, OAuthFlow.status.in_(("pending", "processing")))
                .values(
                    status="error", error_message=_EXPIRED, secrets_encrypted=None, poll_owner=None, poll_after=None
                )
            )
            await session.execute(
                update(OAuthFlow)
                .where(OAuthFlow.status == "verifying", OAuthFlow.verification_deadline <= now)
                .values(status="success", probe_status_code=0, verification_deadline=None)
            )
            await session.commit()

    @staticmethod
    async def mark_bound_save(session: AsyncSession, flow_id: str, *, needs_probe: bool) -> bool:
        now = time.time()
        result = await session.scalar(
            update(OAuthFlow)
            .where(OAuthFlow.id == flow_id, OAuthFlow.status == "processing", OAuthFlow.expires_at > now)
            .values(
                status="verifying" if needs_probe else "success",
                verification_deadline=now + POST_SAVE_TIMEOUT_SECONDS + 10 if needs_probe else None,
            )
            .returning(OAuthFlow.id)
        )
        return result is not None

    @staticmethod
    async def _write(statement: Update) -> bool:
        async with get_background_session() as session:
            result = await session.scalar(statement.returning(OAuthFlow.id))
            await session.commit()
            return result is not None
