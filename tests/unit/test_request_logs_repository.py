from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import ResourceClosedError

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.builders import _cost_summary_from_logs, _usage_metrics


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


@pytest.mark.asyncio
async def test_add_log_ignores_closed_transaction(monkeypatch) -> None:
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        async def _commit_failure() -> None:
            raise ResourceClosedError("This transaction is closed")

        async def _refresh_failure(_: object) -> None:
            raise AssertionError("refresh should not be called after commit failure")

        monkeypatch.setattr(session, "commit", _commit_failure)
        monkeypatch.setattr(session, "refresh", _refresh_failure)

        log = await repo.add_log(
            account_id=None,
            request_id="req",
            model="gpt-5.2",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1,
            status="success",
            error_code=None,
            plan_type="plus",
        )

        assert log.request_id == "req"
        assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_add_log_persists_request_kind(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        saved = await repo.add_log(
            account_id=None,
            request_id="req_kind",
            model="gpt-5.2",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            status="success",
            error_code=None,
            request_kind="warmup",
        )

        persisted = await session.scalar(select(RequestLog).where(RequestLog.id == saved.id))
        assert persisted is not None
        assert persisted.request_kind == "warmup"


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_prefers_session_then_falls_back_to_api_key_scope() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)
    executed_sql: list[str] = []
    returned_values = iter(
        [
            "acc_latest",
            "acc_scoped",
            "acc_session",
            None,
            "acc_scoped",
            None,
        ]
    )

    async def _execute(statement):
        executed_sql.append(str(statement))
        value = next(returned_values)
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    session.execute.side_effect = _execute

    owner_any = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id=None,
    )
    owner_scoped = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
    )
    owner_session = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="sid_terminal_a",
    )
    owner_session_fallback = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="sid_terminal_b",
    )
    owner_missing = await repo.find_latest_account_id_for_response_id(
        response_id="resp_missing_owner",
        api_key_id=None,
    )

    assert owner_any == "acc_latest"
    assert owner_scoped == "acc_scoped"
    assert owner_session == "acc_session"
    assert owner_session_fallback == "acc_scoped"
    assert owner_missing is None
    assert "request_logs.api_key_id = :api_key_id_1" not in executed_sql[0]
    assert "request_logs.api_key_id = :api_key_id_1" in executed_sql[1]
    assert "request_logs.session_id = :session_id_1" in executed_sql[2]
    assert "request_logs.session_id = :session_id_1" in executed_sql[3]
    assert "request_logs.session_id = :session_id_1" not in executed_sql[4]


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_ignores_blank_response_id() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)

    owner = await repo.find_latest_account_id_for_response_id(
        response_id="   ",
        api_key_id="api_key_1",
        session_id="sid_terminal_a",
    )

    assert owner is None
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_ignores_blank_session_id_scope() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)
    executed_sql: list[str] = []

    async def _execute(statement):
        executed_sql.append(str(statement))
        return SimpleNamespace(scalar_one_or_none=lambda: "acc_scoped")

    session.execute.side_effect = _execute

    owner = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="   ",
    )

    assert owner == "acc_scoped"
    assert len(executed_sql) == 1
    assert "request_logs.session_id = :session_id_1" not in executed_sql[0]


@pytest.mark.asyncio
async def test_find_latest_account_id_for_response_id_falls_back_when_session_scope_owner_is_blank() -> None:
    session = AsyncMock()
    repo = RequestLogsRepository(session)
    executed_sql: list[str] = []
    returned_values = iter(["   ", "acc_fallback"])

    async def _execute(statement):
        executed_sql.append(str(statement))
        return SimpleNamespace(scalar_one_or_none=lambda: next(returned_values))

    session.execute.side_effect = _execute

    owner = await repo.find_latest_account_id_for_response_id(
        response_id="resp_lookup_owner",
        api_key_id="api_key_1",
        session_id="sid_terminal_a",
    )

    assert owner == "acc_fallback"
    assert len(executed_sql) == 2
    assert "request_logs.session_id = :session_id_1" in executed_sql[0]
    assert "request_logs.session_id = :session_id_1" not in executed_sql[1]


def _reference_logs_for_accounts(
    logs: list[RequestLog],
    account_ids: set[str],
    *,
    include_unattributed: bool,
) -> list[RequestLog]:
    # Mirrors the old `app.modules.usage.service._logs_for_accounts` filter
    # that `aggregate_usage_window` now replaces with SQL.
    if include_unattributed:
        return [log for log in logs if log.account_id is None or log.account_id in account_ids]
    return [log for log in logs if log.account_id in account_ids]


@pytest.mark.asyncio
async def test_aggregate_usage_window_matches_python_reference(db_setup) -> None:
    del db_setup
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        accounts_repo = AccountsRepository(session)
        for account_id in ("acc1", "acc2", "acc3", "acc4"):
            await accounts_repo.upsert(_make_account(account_id, f"{account_id}@example.com"))
        now = utcnow()
        since = now - timedelta(days=7)
        account_ids = {"acc1", "acc2", "acc3"}

        # (account_id, model, input_tokens, output_tokens, cached_input_tokens,
        #  reasoning_tokens, status, error_code)
        rows_spec = [
            ("acc1", "gpt-5.1", 1000, 500, 200, None, "success", None),
            ("acc1", "gpt-5.1", 300, None, 50, 120, "success", None),  # output falls back to reasoning
            ("acc1", "gpt-5.2", 10, 5, None, None, "error", "rate_limit_exceeded"),
            ("acc1", "gpt-5.2", 20, 0, 500, None, "error", "rate_limit_exceeded"),  # cached clamps to input
            ("acc2", "gpt-5.1", 400, 100, 0, None, "success", None),
            ("acc2", "gpt-5.1", 250, 50, None, None, "error", "quota_exceeded"),
            ("acc2", "totally-unknown-model-zzz", 90, 10, 10, None, "error", "quota_exceeded"),  # null cost
            ("acc2", "totally-unknown-model-zzz", 15, 5, None, None, "success", None),  # null cost
            ("acc3", "gpt-5.2", 60, 40, 20, None, "success", None),
            ("acc3", "gpt-5.2", 5, None, None, 15, "error", "rate_limit_exceeded"),
            ("acc4", "gpt-5.1", 1000, 1000, 100, None, "success", None),  # excluded account
            (None, "gpt-5.1", 45, 15, 5, None, "success", None),  # unattributed
            (None, "gpt-5.2", 70, 30, None, None, "error", "quota_exceeded"),  # unattributed
            ("acc1", "gpt-5.1", 5, 5, None, None, "success", None),
            ("acc2", "gpt-5.2", 8, 2, None, None, "success", None),
            ("acc3", "gpt-5.1", 12, 3, None, None, "error", "server_error"),
            ("acc1", "gpt-5.2", 33, 7, 40, None, "success", None),  # cached clamps to input
            ("acc2", "gpt-5.1", 9, 1, 3, None, "success", None),
            ("acc3", "gpt-5.2", 21, 4, None, None, "success", None),
            ("acc1", "totally-unknown-model-zzz", 100, 100, 100, None, "error", "rate_limit_exceeded"),  # null cost
        ]

        for i, (account_id, model, input_tokens, output_tokens, cached, reasoning, status, error_code) in enumerate(
            rows_spec
        ):
            await repo.add_log(
                account_id=account_id,
                request_id=f"req_parity_{i}",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached,
                reasoning_tokens=reasoning,
                latency_ms=10,
                status=status,
                error_code=error_code,
                requested_at=now - timedelta(hours=1, minutes=i),
            )

        all_logs = await repo.list_since(since)
        assert len(all_logs) == len(rows_spec)

        for include_unattributed in (True, False):
            filtered = _reference_logs_for_accounts(all_logs, account_ids, include_unattributed=include_unattributed)
            expected_metrics = _usage_metrics(filtered)
            expected_cost = _cost_summary_from_logs(filtered)

            actual_metrics, actual_cost = await repo.aggregate_usage_window(
                since,
                account_ids=account_ids,
                include_unattributed=include_unattributed,
            )

            assert actual_metrics == expected_metrics
            assert actual_cost.currency == expected_cost.currency
            assert actual_cost.total_usd_7d == pytest.approx(expected_cost.total_usd_7d)
            assert [entry.model for entry in actual_cost.by_model] == [
                entry.model for entry in expected_cost.by_model
            ]
            for actual_entry, expected_entry in zip(actual_cost.by_model, expected_cost.by_model, strict=True):
                assert actual_entry.usd == pytest.approx(expected_entry.usd)
