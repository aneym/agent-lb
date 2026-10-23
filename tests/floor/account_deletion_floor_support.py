"""Background (chunked) account deletion: drain, fold interleave, restart,
idempotency, and supersede semantics for both delete_history variants."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import Table, func, select, text, update
from sqlalchemy.sql import Select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AccountUsageRollup,
    RequestDemandQuarterRollup,
    RequestLog,
    RequestUsageHourlyRollup,
    StickySession,
    StickySessionKind,
    UsageHistory,
)
from app.db.session import SessionLocal, get_background_session, sqlite_writer_section
from app.modules.accounts.deletion import _request_logs_chunk, run_account_deletion_pass
from app.modules.accounts.repository import ACCOUNT_PENDING_DELETION_REASON, AccountsRepository
from app.modules.accounts.usage_rollup import run_fold_pass
from app.modules.accounts.usage_time_rollup import run_hourly_fold_pass, to_dimension
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

_ORPHAN_DIMENSION = to_dimension(None)


@pytest.fixture(autouse=True)
def _no_background_wake(monkeypatch):
    """Keep the drain under explicit test control.

    The suite's stand-in leader election runs scheduler bodies inline, so the
    delete API's worker wake would drain accounts concurrently with (and race)
    the passes these tests drive step by step. The scheduler's own tick (one
    pass at startup plus every interval) is neutralized as well: a tick firing
    between a DELETE and the assertions would drain the account these tests
    expect to still be marked.
    """
    monkeypatch.setattr("app.modules.accounts.lifecycle.request_account_deletion_run", lambda: None)

    async def _no_tick(self) -> None:
        return None

    monkeypatch.setattr("app.modules.accounts.deletion.AccountDeletionScheduler._run_once", _no_tick)


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _add_log(logs_repo: RequestLogsRepository, *, account_id: str, request_id: str, requested_at) -> None:
    await logs_repo.add_log(
        account_id=account_id,
        request_id=request_id,
        model="gpt-5.1-codex",
        input_tokens=100,
        output_tokens=50,
        latency_ms=100,
        status="success",
        error_code=None,
        requested_at=requested_at,
        cost_usd=0.01,
    )


async def _seed_account(account_id: str, *, log_count: int, usage_count: int = 0, requested_at=None) -> None:
    requested_at = requested_at or (utcnow() - timedelta(days=2))
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account(account_id, f"{account_id}@example.com"))
        for index in range(log_count):
            await _add_log(
                logs_repo,
                account_id=account_id,
                request_id=f"req_{account_id}_{index}",
                requested_at=requested_at,
            )
        for index in range(usage_count):
            await usage_repo.add_entry(account_id, float(index), window="primary")


async def _account_row(account_id: str) -> Account | None:
    async with SessionLocal() as session:
        return await session.get(Account, account_id)


async def _attached_log_count(account_id: str) -> int:
    async with SessionLocal() as session:
        return (
            await session.execute(select(func.count(RequestLog.id)).where(RequestLog.account_id == account_id))
        ).scalar_one()


async def _log_rows(prefix: str) -> list[RequestLog]:
    async with SessionLocal() as session:
        return list(
            (await session.execute(select(RequestLog).where(RequestLog.request_id.like(f"req_{prefix}%"))))
            .scalars()
            .all()
        )


async def _hourly_rows_for_dimension(dimension: str) -> list[RequestUsageHourlyRollup]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(RequestUsageHourlyRollup).where(RequestUsageHourlyRollup.account_id == dimension)
                )
            )
            .scalars()
            .all()
        )


async def _demand_rows_for_dimension(dimension: str) -> list[RequestDemandQuarterRollup]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(RequestDemandQuarterRollup).where(RequestDemandQuarterRollup.account_id == dimension)
                )
            )
            .scalars()
            .all()
        )


async def _lifetime_rollup(account_id: str) -> AccountUsageRollup | None:
    async with SessionLocal() as session:
        return await session.get(AccountUsageRollup, account_id)


async def _run_one_detach_chunk(account_id: str, *, batch_size: int, delete_history: bool = False) -> int:
    async with get_background_session() as session:
        async with sqlite_writer_section():
            affected = await _request_logs_chunk(
                session, account_id, delete_history=delete_history, batch_size=batch_size
            )
            await session.commit()
    return affected


def _fake_id_token(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


async def _legacy_replace_credentials(account_id: str, encryptor: TokenEncryptor) -> None:
    """Mimic a credential replacement by a pre-upgrade replica: fresh
    ciphertext and status, but the marker columns its ORM does not know stay
    untouched."""
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                access_token_encrypted=encryptor.encrypt("fresh-access"),
                refresh_token_encrypted=encryptor.encrypt("fresh-refresh"),
                id_token_encrypted=encryptor.encrypt("fresh-id"),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()


def _batch_pinning_cases() -> tuple[tuple[Callable[[str, int], Select[tuple[int]]], Table, str], ...]:
    from app.db.models import AdditionalUsageHistory
    from app.modules.accounts import deletion

    return (
        (deletion._usage_history_batch, cast("Table", UsageHistory.__table__), "idx_usage_account_time"),
        (
            deletion._additional_usage_history_batch,
            cast("Table", AdditionalUsageHistory.__table__),
            "ix_additional_usage_distinct_labels",
        ),
        (deletion._request_logs_batch, cast("Table", RequestLog.__table__), "idx_logs_account_kind_deleted_latest"),
    )


__all__ = [
    "ACCOUNT_PENDING_DELETION_REASON",
    "Account",
    "AccountStatus",
    "AccountsRepository",
    "RequestLog",
    "RequestLogsRepository",
    "SessionLocal",
    "StickySession",
    "StickySessionKind",
    "TokenEncryptor",
    "UsageHistory",
    "_ORPHAN_DIMENSION",
    "_account_row",
    "_add_log",
    "_attached_log_count",
    "_batch_pinning_cases",
    "_demand_rows_for_dimension",
    "_fake_id_token",
    "_hourly_rows_for_dimension",
    "_legacy_replace_credentials",
    "_lifetime_rollup",
    "_log_rows",
    "_make_account",
    "_no_background_wake",
    "_run_one_detach_chunk",
    "_seed_account",
    "func",
    "json",
    "pytest",
    "run_account_deletion_pass",
    "run_fold_pass",
    "run_hourly_fold_pass",
    "select",
    "text",
    "timedelta",
    "to_dimension",
    "update",
    "utcnow",
]
