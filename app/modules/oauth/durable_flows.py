from __future__ import annotations

from app.core.utils.time import naive_utc_to_epoch
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.oauth.flow_constants import _TERMINAL_OAUTH_STATUSES
from app.modules.oauth.repository import OAuthFlowRecord, OAuthFlowRepository
from app.modules.oauth.state import _MAX_RETAINED_TERMINAL_OAUTH_FLOWS as _MAX_RETAINED_TERMINAL_OAUTH_FLOWS
from app.modules.oauth.state import OAuthState


class OAuthDurableFlows:
    async def _persist_flow_record(self, record: OAuthFlowRecord) -> None:
        async with get_background_session() as session:
            repo = OAuthFlowRepository(session, self._encryptor)
            await repo.purge_expired(terminal_keep=_MAX_RETAINED_TERMINAL_OAUTH_FLOWS)
            await repo.create(record)

    async def _claim_device_slot(self, flow_id: str) -> None:
        async with sqlite_writer_section():
            async with get_background_session() as session:
                await OAuthFlowRepository(session, self._encryptor).claim_device_slot(flow_id)

    async def _consume_device_slot(self, flow_id: str | None) -> bool:
        if flow_id is None:
            return False
        async with sqlite_writer_section():
            async with get_background_session() as session:
                return await OAuthFlowRepository(session, self._encryptor).consume_device_slot(flow_id)

    async def _persist_flow_status(self, flow_id: str, *, status: str, error_message: str | None) -> bool:
        async with get_background_session() as session:
            repo = OAuthFlowRepository(session, self._encryptor)
            return await repo.set_status(flow_id, status=status, error_message=error_message)

    async def _load_flow_record(
        self, *, flow_id: str | None = None, state_token: str | None = None
    ) -> OAuthFlowRecord | None:
        async with get_background_session() as session:
            repo = OAuthFlowRepository(session, self._encryptor)
            if flow_id is not None:
                return await repo.get_by_flow_id(flow_id)
            if state_token is not None:
                return await repo.get_by_state_token(state_token)
        return None

    @staticmethod
    def _record_to_state(record: OAuthFlowRecord) -> OAuthState:
        return OAuthState(
            flow_id=record.flow_id,
            status=record.status,
            method=record.method,
            provider=record.provider,
            error_message=record.error_message,
            state_token=record.state_token,
            intended_account_id=record.intended_account_id,
            code_verifier=record.code_verifier,
            device_auth_id=record.device_auth_id,
            user_code=record.user_code,
            interval_seconds=record.interval_seconds,
            expires_at=naive_utc_to_epoch(record.expires_at) if record.expires_at is not None else None,
            finished_at=naive_utc_to_epoch(record.finished_at) if record.finished_at is not None else None,
        )

    async def _reconcile_flow_from_durable(
        self, *, flow_id: str | None = None, state_token: str | None = None
    ) -> OAuthFlowRecord | None:
        record = await self._load_flow_record(flow_id=flow_id, state_token=state_token)
        async with self._store.lock:
            local = self._store.get_flow_locked(flow_id) if flow_id is not None else None
            if local is None and state_token is not None:
                local = self._store.get_flow_by_state_token_locked(state_token)
            if record is None:
                if local is not None and local.status not in _TERMINAL_OAUTH_STATUSES:
                    self._store.remove_flow_locked(local)
                return None
            if record.status in _TERMINAL_OAUTH_STATUSES:
                if local is None:
                    self._store.remember_flow_locked(self._record_to_state(record))
                elif local.status != record.status:
                    self._store.set_flow_status_locked(local, status=record.status, error_message=record.error_message)
                return record
            if local is None:
                self._store.remember_flow_locked(self._record_to_state(record))
        return record
