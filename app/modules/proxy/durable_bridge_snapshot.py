from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db.session_models import HttpBridgeSessionRecord, HttpBridgeSessionState


@dataclass(frozen=True, slots=True)
class DurableBridgeSessionSnapshot:
    id: str
    session_key_kind: str
    session_key_value: str
    session_key_hash: str
    api_key_scope: str
    owner_instance_id: str | None
    owner_epoch: int
    lease_expires_at: datetime | None
    state: HttpBridgeSessionState
    account_id: str | None
    model: str | None
    service_tier: str | None
    latest_turn_state: str | None
    latest_response_id: str | None
    latest_input_item_count: int | None
    latest_input_full_fingerprint: str | None
    closed_at: datetime | None


def _to_snapshot(row: HttpBridgeSessionRecord | None) -> DurableBridgeSessionSnapshot | None:
    if row is None:
        return None
    return DurableBridgeSessionSnapshot(
        id=row.id,
        session_key_kind=row.session_key_kind,
        session_key_value=row.session_key_value,
        session_key_hash=row.session_key_hash,
        api_key_scope=row.api_key_scope,
        owner_instance_id=row.owner_instance_id,
        owner_epoch=row.owner_epoch,
        lease_expires_at=row.lease_expires_at,
        state=row.state,
        account_id=row.account_id,
        model=row.model,
        service_tier=row.service_tier,
        latest_turn_state=row.latest_turn_state,
        latest_response_id=row.latest_response_id,
        latest_input_item_count=row.latest_input_item_count,
        latest_input_full_fingerprint=row.latest_input_full_fingerprint,
        closed_at=row.closed_at,
    )


def _to_snapshot_required(row: HttpBridgeSessionRecord) -> DurableBridgeSessionSnapshot:
    snapshot = _to_snapshot(row)
    if snapshot is None:
        raise RuntimeError("Expected durable bridge session snapshot")
    return snapshot
