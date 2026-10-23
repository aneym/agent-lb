from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountUsageRollup, AccountUsageRollupState
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository, _clear_request_usage_summary_cache
from app.modules.accounts.service_caches import clear_account_caches
from app.modules.accounts.usage_rollup import FOLD_LAG, run_fold_pass
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


def _make_account(
    account_id: str, email: str, plan_type: str = "plus", chatgpt_account_id: str | None = None
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        chatgpt_account_id=chatgpt_account_id,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _add_log(
    logs_repo: RequestLogsRepository,
    *,
    account_id: str,
    request_id: str,
    requested_at,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cached_input_tokens: int = 0,
    cost_usd: float | None = 0.01,
    request_kind: str = "normal",
):
    return await logs_repo.add_log(
        account_id=account_id,
        request_id=request_id,
        model="gpt-5.1-codex",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        latency_ms=100,
        status="success",
        error_code=None,
        requested_at=requested_at,
        cost_usd=cost_usd,
        request_kind=request_kind,
    )


async def _summaries(account_ids: list[str] | None = None):
    async with SessionLocal() as session:
        return await AccountsRepository(session).list_request_usage_summary_by_account(account_ids)


async def _rollup_rows():
    async with SessionLocal() as session:
        return (await session.execute(select(AccountUsageRollup))).scalars().all()


async def _watermark():
    async with SessionLocal() as session:
        return (
            await session.execute(select(AccountUsageRollupState.folded_through).where(AccountUsageRollupState.id == 1))
        ).scalar_one_or_none()


__all__ = [
    "AccountsRepository",
    "FOLD_LAG",
    "RequestLogsRepository",
    "SessionLocal",
    "_add_log",
    "_clear_request_usage_summary_cache",
    "_make_account",
    "_rollup_rows",
    "_summaries",
    "_watermark",
    "asyncio",
    "clear_account_caches",
    "pytest",
    "run_fold_pass",
    "timedelta",
    "utcnow",
]
