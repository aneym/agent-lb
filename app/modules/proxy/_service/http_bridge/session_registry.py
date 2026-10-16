from __future__ import annotations

import logging

from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_durable_lease_ttl_seconds,
    _http_bridge_previous_response_alias_key,
    _http_bridge_turn_state_alias_key,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.http_bridge.service_stubs import _service_get_settings
from app.modules.proxy._service.support import _HTTPBridgeSession, _HTTPBridgeSessionKey

logger = logging.getLogger("app.modules.proxy.service")


class _HTTPBridgeSessionRegistryMixin(_HTTPBridgeServiceProtocol):
    async def _register_http_bridge_turn_state(self, session: "_HTTPBridgeSession", turn_state: str) -> None:
        async with self._http_bridge_lock:
            if session.closed:
                return
            session.downstream_turn_state_aliases.add(turn_state)
            if session.downstream_turn_state is None:
                session.downstream_turn_state = turn_state
            for alias in session.downstream_turn_state_aliases:
                self._http_bridge_turn_state_index[_http_bridge_turn_state_alias_key(alias, session.key.api_key_id)] = (
                    session.key
                )
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.register_turn_state(
                    session_id=session.durable_session_id,
                    api_key_id=session.key.api_key_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    turn_state=turn_state,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                )
            except Exception:
                logger.warning("Failed to persist durable HTTP bridge turn-state alias", exc_info=True)

    async def _register_http_bridge_previous_response_id(
        self,
        session: "_HTTPBridgeSession",
        response_id: str,
        *,
        input_item_count: int | None = None,
        input_full_fingerprint: str | None = None,
    ) -> None:
        stripped_response_id = response_id.strip()
        if not stripped_response_id:
            return
        async with self._http_bridge_lock:
            if session.closed:
                return
            if (
                session.upstream_control.retire_after_drain
                and self._http_bridge_sessions.get(session.key) is not session
            ):
                return
            alias_key = _http_bridge_previous_response_alias_key(stripped_response_id, session.key.api_key_id)
            self._http_bridge_previous_response_index[alias_key] = session.key
            session.previous_response_ids.add(stripped_response_id)
        if session.durable_session_id is not None and session.durable_owner_epoch is not None:
            try:
                await self._durable_bridge.register_previous_response_id(
                    session_id=session.durable_session_id,
                    api_key_id=session.key.api_key_id,
                    instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
                    owner_epoch=session.durable_owner_epoch,
                    response_id=stripped_response_id,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                    input_item_count=input_item_count,
                    input_full_fingerprint=input_full_fingerprint,
                )
            except Exception:
                logger.warning("Failed to persist durable HTTP bridge previous_response_id alias", exc_info=True)

    async def _unregister_http_bridge_turn_states(self, session: "_HTTPBridgeSession") -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_turn_states_locked(session)

    async def _unregister_http_bridge_previous_response_ids(self, session: "_HTTPBridgeSession") -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_previous_response_ids_locked(session)

    def _detach_http_bridge_session_locked(
        self,
        key: "_HTTPBridgeSessionKey",
        *,
        expected_session: "_HTTPBridgeSession | None" = None,
        mark_closed: bool = True,
    ) -> "_HTTPBridgeSession | None":
        session = self._http_bridge_sessions.get(key)
        if session is None:
            return None
        if expected_session is not None and session is not expected_session:
            return None
        self._http_bridge_sessions.pop(key, None)
        if mark_closed:
            session.closed = True
        self._unregister_http_bridge_turn_states_locked(session)
        self._unregister_http_bridge_previous_response_ids_locked(session)
        return session

    def _unregister_http_bridge_turn_states_locked(self, session: "_HTTPBridgeSession") -> None:
        aliases = tuple(session.downstream_turn_state_aliases)
        current_session = self._http_bridge_sessions.get(session.key)
        for alias in aliases:
            alias_key = _http_bridge_turn_state_alias_key(alias, session.key.api_key_id)
            if (
                current_session is not None
                and current_session is not session
                and alias in current_session.downstream_turn_state_aliases
            ):
                continue
            if self._http_bridge_turn_state_index.get(alias_key) == session.key:
                self._http_bridge_turn_state_index.pop(alias_key, None)
        session.downstream_turn_state_aliases.clear()

    def _unregister_http_bridge_previous_response_ids_locked(self, session: "_HTTPBridgeSession") -> None:
        response_ids = tuple(session.previous_response_ids)
        current_session = self._http_bridge_sessions.get(session.key)
        for response_id in response_ids:
            alias_key = _http_bridge_previous_response_alias_key(response_id, session.key.api_key_id)
            if (
                current_session is not None
                and current_session is not session
                and response_id in current_session.previous_response_ids
            ):
                continue
            if self._http_bridge_previous_response_index.get(alias_key) == session.key:
                self._http_bridge_previous_response_index.pop(alias_key, None)
        session.previous_response_ids.clear()
