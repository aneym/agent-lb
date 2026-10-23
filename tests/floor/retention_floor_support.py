from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

import app.core.retention.job as retention_job
from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.retention.job import run_retention_pass
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, RequestLog, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository, _clear_request_usage_summary_cache
from app.modules.accounts.service_caches import clear_account_caches
from app.modules.accounts.usage_rollup import run_fold_pass
from app.modules.accounts.usage_time_rollup import run_conversation_fold_pass, run_hourly_fold_pass
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


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


async def _add_log(
    logs_repo: RequestLogsRepository, *, account_id: str, request_id: str, requested_at, conversation_id=None
):
    return await logs_repo.add_log(
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
        conversation_id=conversation_id,
    )


def _set_retention(monkeypatch, *, request_logs: int = 0, usage_history: int = 0) -> None:
    monkeypatch.setenv("AGENT_LB_REQUEST_LOG_RETENTION_DAYS", str(request_logs))
    monkeypatch.setenv("AGENT_LB_USAGE_HISTORY_RETENTION_DAYS", str(usage_history))
    get_settings.cache_clear()


async def _set_dashboard_retention(*, request_logs: int | None = None, usage_history: int | None = None) -> None:
    from app.core.config.settings_cache import get_settings_cache
    from app.modules.settings.repository import SettingsRepository

    async with SessionLocal() as session:
        row = await SettingsRepository(session).get_or_create()
        row.request_log_retention_days = request_logs
        row.usage_history_retention_days = usage_history
        await session.commit()
    await get_settings_cache().invalidate()


__all__ = [
    "AccountsRepository",
    "AdditionalUsageHistory",
    "RequestLog",
    "RequestLogsRepository",
    "SessionLocal",
    "Settings",
    "UsageHistory",
    "ValidationError",
    "_add_log",
    "_clear_request_usage_summary_cache",
    "_make_account",
    "_set_dashboard_retention",
    "_set_retention",
    "clear_account_caches",
    "pytest",
    "retention_job",
    "run_conversation_fold_pass",
    "run_fold_pass",
    "run_hourly_fold_pass",
    "run_retention_pass",
    "select",
    "timedelta",
    "utcnow",
]
