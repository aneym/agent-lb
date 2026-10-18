from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.settings.repository import SettingsRepository


async def _create_accounts() -> list[Account]:
    encryptor = TokenEncryptor()
    accounts = [
        Account(
            id="sticky-api-a",
            chatgpt_account_id="sticky-api-a",
            email="sticky-a@example.com",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access-a"),
            refresh_token_encrypted=encryptor.encrypt("refresh-a"),
            id_token_encrypted=encryptor.encrypt("id-a"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        ),
        Account(
            id="sticky-api-b",
            chatgpt_account_id="sticky-api-b",
            email="sticky-b@example.com",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access-b"),
            refresh_token_encrypted=encryptor.encrypt("refresh-b"),
            id_token_encrypted=encryptor.encrypt("id-b"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        ),
    ]
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        for account in accounts:
            await repo.upsert(account)
    return accounts


async def _set_affinity_ttl(seconds: int) -> None:
    async with SessionLocal() as session:
        settings = await SettingsRepository(session).get_or_create()
        settings.openai_cache_affinity_max_age_seconds = seconds
        await session.commit()


async def _insert_sticky_session(
    *,
    key: str,
    account_id: str,
    kind: StickySessionKind,
    updated_at_offset_seconds: int,
) -> None:
    timestamp = utcnow() - timedelta(seconds=updated_at_offset_seconds)
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO sticky_sessions (key, account_id, kind, created_at, updated_at)
                VALUES (:key, :account_id, :kind, :timestamp, :timestamp)
                """
            ),
            {
                "key": key,
                "account_id": account_id,
                "kind": kind.value,
                "timestamp": timestamp,
            },
        )
        await session.commit()


async def _insert_http_bridge_session(
    *,
    session_id: str,
    state: str,
    last_seen_offset_seconds: int,
) -> None:
    timestamp = utcnow() - timedelta(seconds=last_seen_offset_seconds)
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO http_bridge_sessions (
                    id, session_key_kind, session_key_value, session_key_hash, api_key_scope,
                    owner_epoch, state, last_seen_at, created_at, updated_at
                )
                VALUES (
                    :id, 'session_header', :key_value, :key_hash, '__anonymous__',
                    1, :state, :timestamp, :timestamp, :timestamp
                )
                """
            ),
            {
                "id": session_id,
                "key_value": f"{session_id}-key",
                "key_hash": f"{session_id}-hash",
                "state": state,
                "timestamp": timestamp,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO http_bridge_session_aliases (
                    id, session_id, alias_kind, alias_value, alias_hash, api_key_scope, created_at, updated_at
                )
                VALUES (
                    :alias_id, :session_id, 'previous_response_id', :alias_value, :alias_hash,
                    '__anonymous__', :timestamp, :timestamp
                )
                """
            ),
            {
                "alias_id": f"{session_id}-alias",
                "session_id": session_id,
                "alias_value": f"{session_id}-response",
                "alias_hash": f"{session_id}-alias-hash",
                "timestamp": timestamp,
            },
        )
        await session.commit()
