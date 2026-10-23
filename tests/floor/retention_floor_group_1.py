from __future__ import annotations

from tests.floor.retention_floor_support import (
    AccountsRepository,
    AdditionalUsageHistory,
    RequestLog,
    RequestLogsRepository,
    SessionLocal,
    Settings,
    UsageHistory,
    ValidationError,
    _add_log,
    _clear_request_usage_summary_cache,
    _make_account,
    _set_retention,
    clear_account_caches,
    pytest,
    retention_job,
    run_conversation_fold_pass,
    run_fold_pass,
    run_hourly_fold_pass,
    run_retention_pass,
    select,
    timedelta,
    utcnow,
)


def test_retention_settings_validation():
    assert Settings(request_log_retention_days=0).request_log_retention_days == 0
    assert Settings(request_log_retention_days=30).request_log_retention_days == 30
    assert Settings(usage_history_retention_days=45).usage_history_retention_days == 45
    with pytest.raises(ValidationError):
        Settings(request_log_retention_days=7)
    with pytest.raises(ValidationError):
        Settings(usage_history_retention_days=30)


@pytest.mark.asyncio
async def test_retention_disabled_by_default_deletes_nothing(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_off", "off@example.com"))
        await _add_log(logs_repo, account_id="acc_off", request_id="req_old", requested_at=now - timedelta(days=400))
        session.add(UsageHistory(account_id="acc_off", used_percent=10.0, recorded_at=now - timedelta(days=400)))
        await session.commit()

    deleted = await run_retention_pass(now=now)
    assert deleted == {"request_logs": 0, "usage_history": 0, "additional_usage_history": 0}


@pytest.mark.asyncio
async def test_request_log_pruning_respects_watermark_and_preserves_totals(async_client, db_setup, monkeypatch):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_ret", "ret@example.com"))
        await _add_log(logs_repo, account_id="acc_ret", request_id="req_60d", requested_at=now - timedelta(days=60))
        await _add_log(logs_repo, account_id="acc_ret", request_id="req_40d", requested_at=now - timedelta(days=40))
        await _add_log(logs_repo, account_id="acc_ret", request_id="req_1d", requested_at=now - timedelta(days=1))

    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    await run_conversation_fold_pass(now=now)

    async def _request_usage():
        _clear_request_usage_summary_cache()
        clear_account_caches()
        response = await async_client.get("/api/accounts?fresh=true")
        assert response.status_code == 200
        accounts = {item["accountId"]: item for item in response.json()["accounts"]}
        return accounts["acc_ret"]["requestUsage"]

    before = await _request_usage()
    assert before["requestCount"] == 3

    _set_retention(monkeypatch, request_logs=30)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 2

    async with SessionLocal() as session:
        remaining = (await session.execute(select(RequestLog.request_id))).scalars().all()
    assert sorted(remaining) == ["req_1d"]

    assert await _request_usage() == before


@pytest.mark.asyncio
async def test_request_log_pruning_skipped_without_watermark(db_setup, monkeypatch):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_nofold", "nofold@example.com"))
        await _add_log(logs_repo, account_id="acc_nofold", request_id="req_old", requested_at=now - timedelta(days=400))

    _set_retention(monkeypatch, request_logs=30)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 0
    async with SessionLocal() as session:
        assert (await session.execute(select(RequestLog.id))).scalars().all()


@pytest.mark.asyncio
async def test_usage_history_pruning_keeps_latest_per_identity(db_setup, monkeypatch):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_idle", "idle@example.com"))
        for days_ago in (200, 150, 100):
            session.add(
                UsageHistory(
                    account_id="acc_idle",
                    window="primary",
                    used_percent=float(days_ago),
                    recorded_at=now - timedelta(days=days_ago),
                )
            )
            session.add(
                AdditionalUsageHistory(
                    account_id="acc_idle",
                    quota_key="gpt-5-pro",
                    limit_name="gpt5pro",
                    metered_feature="gpt5pro",
                    window="primary",
                    used_percent=float(days_ago),
                    recorded_at=now - timedelta(days=days_ago),
                )
            )
        await session.commit()

    _set_retention(monkeypatch, usage_history=45)
    deleted = await run_retention_pass(now=now)
    assert deleted["usage_history"] == 2
    assert deleted["additional_usage_history"] == 2

    async with SessionLocal() as session:
        usage_rows = (await session.execute(select(UsageHistory))).scalars().all()
        additional_rows = (await session.execute(select(AdditionalUsageHistory))).scalars().all()
    assert len(usage_rows) == 1
    assert usage_rows[0].used_percent == 100.0
    assert len(additional_rows) == 1
    assert additional_rows[0].used_percent == 100.0


@pytest.mark.asyncio
async def test_pruning_drains_backlog_across_batches(db_setup, monkeypatch):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_batch", "batch@example.com"))
        for index in range(5):
            await _add_log(
                logs_repo,
                account_id="acc_batch",
                request_id=f"req_{index}",
                requested_at=now - timedelta(days=60, minutes=index),
            )

    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    await run_conversation_fold_pass(now=now)
    _set_retention(monkeypatch, request_logs=30)
    monkeypatch.setattr(retention_job, "BATCH_SIZE", 2)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 5
    async with SessionLocal() as session:
        assert (await session.execute(select(RequestLog.id))).scalars().all() == []


@pytest.mark.asyncio
async def test_request_log_pruning_skipped_while_fold_is_not_current(async_client, db_setup, monkeypatch):
    """A stalled fold (watermark older than two fold lags) must skip pruning
    entirely: unfolded rows above the watermark would otherwise be lost, and
    concurrent readers hold watermarks near the stale one."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_stall", "stall@example.com"))
        await _add_log(logs_repo, account_id="acc_stall", request_id="req_60d", requested_at=now - timedelta(days=60))
        await _add_log(logs_repo, account_id="acc_stall", request_id="req_40d", requested_at=now - timedelta(days=40))
        await _add_log(logs_repo, account_id="acc_stall", request_id="req_1d", requested_at=now - timedelta(days=1))

    # Fold stalled 50 days ago: watermark = (now - 50d) - FOLD_LAG.
    await run_fold_pass(now=now - timedelta(days=50))
    await run_hourly_fold_pass(now=now - timedelta(days=50))
    await run_conversation_fold_pass(now=now - timedelta(days=50))

    async def _request_usage():
        _clear_request_usage_summary_cache()
        clear_account_caches()
        response = await async_client.get("/api/accounts?fresh=true")
        assert response.status_code == 200
        accounts = {item["accountId"]: item for item in response.json()["accounts"]}
        return accounts["acc_stall"]["requestUsage"]

    before = await _request_usage()
    assert before["requestCount"] == 3

    _set_retention(monkeypatch, request_logs=30)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 0

    async with SessionLocal() as session:
        remaining = (await session.execute(select(RequestLog.request_id))).scalars().all()
    assert sorted(remaining) == ["req_1d", "req_40d", "req_60d"]

    assert await _request_usage() == before


@pytest.mark.asyncio
async def test_request_log_pruning_skipped_while_hourly_backfill_behind(db_setup, monkeypatch):
    """min-gate: a current lifetime watermark alone must not enable pruning
    while the hourly time-axis backfill has not caught up (its watermark
    still at the epoch) — pruning unfolded raw would leave permanent holes
    in the time-series rollups. Once the hourly fold catches up, pruning
    resumes and the folded buckets survive it unchanged."""
    from sqlalchemy import select as sa_select

    from app.db.models import RequestDemandQuarterRollup, RequestUsageHourlyRollup

    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_gate", "gate@example.com"))
        await _add_log(logs_repo, account_id="acc_gate", request_id="req_60d", requested_at=now - timedelta(days=60))
        await _add_log(logs_repo, account_id="acc_gate", request_id="req_1d", requested_at=now - timedelta(days=1))

    # Lifetime fold current, hourly fold never ran (watermark at epoch).
    await run_fold_pass(now=now)
    _set_retention(monkeypatch, request_logs=30)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 0

    # Hourly fold catches up -> min watermark is current -> pruning resumes.
    await run_hourly_fold_pass(now=now)
    await run_conversation_fold_pass(now=now)
    async with SessionLocal() as session:
        hourly_before = sorted(
            (await session.execute(sa_select(RequestUsageHourlyRollup))).scalars().all(),
            key=lambda row: (row.bucket_epoch, row.account_id, row.model),
        )
        hourly_before = [
            (row.bucket_epoch, row.account_id, row.request_count, row.input_tokens) for row in hourly_before
        ]
        demand_before = len((await session.execute(sa_select(RequestDemandQuarterRollup))).scalars().all())
    assert hourly_before  # the 60d row folded

    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 1

    # Folded time-axis buckets are untouched by raw pruning.
    async with SessionLocal() as session:
        hourly_after = sorted(
            (await session.execute(sa_select(RequestUsageHourlyRollup))).scalars().all(),
            key=lambda row: (row.bucket_epoch, row.account_id, row.model),
        )
        hourly_after = [(row.bucket_epoch, row.account_id, row.request_count, row.input_tokens) for row in hourly_after]
        demand_after = len((await session.execute(sa_select(RequestDemandQuarterRollup))).scalars().all())
    assert hourly_after == hourly_before
    assert demand_after == demand_before


@pytest.mark.asyncio
async def test_request_log_pruning_skipped_while_hourly_fold_stalled(db_setup, monkeypatch):
    """min-gate: a stalled HOURLY watermark (older than two fold lags) must
    pause pruning even though the lifetime fold is perfectly current."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_hstall", "hstall@example.com"))
        await _add_log(logs_repo, account_id="acc_hstall", request_id="req_60d", requested_at=now - timedelta(days=60))
        await _add_log(logs_repo, account_id="acc_hstall", request_id="req_1d", requested_at=now - timedelta(days=1))

    await run_hourly_fold_pass(now=now - timedelta(days=50))
    await run_conversation_fold_pass(now=now - timedelta(days=50))
    await run_fold_pass(now=now)

    _set_retention(monkeypatch, request_logs=30)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 0
    async with SessionLocal() as session:
        remaining = (await session.execute(select(RequestLog.request_id))).scalars().all()
    assert sorted(remaining) == ["req_1d", "req_60d"]


@pytest.mark.asyncio
async def test_request_log_pruning_skipped_while_conversation_backfill_behind(db_setup, monkeypatch):
    """min-gate: the conversation satellite participates in the prune gate —
    lifetime and hourly currency alone must not enable pruning while the
    conversation backfill is still at the epoch, or raw the satellite never
    folded would be lost. Once it catches up, pruning resumes and the folded
    presence survives the prune."""
    from sqlalchemy import select as sa_select

    from app.db.models import RequestConversationHourlyRollup

    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_cgate", "cgate@example.com"))
        await _add_log(
            logs_repo,
            account_id="acc_cgate",
            request_id="req_60d",
            requested_at=now - timedelta(days=60),
            conversation_id="conv_gate",
        )
        await _add_log(logs_repo, account_id="acc_cgate", request_id="req_1d", requested_at=now - timedelta(days=1))

    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    _set_retention(monkeypatch, request_logs=30)
    assert (await run_retention_pass(now=now))["request_logs"] == 0

    await run_conversation_fold_pass(now=now)
    assert (await run_retention_pass(now=now))["request_logs"] == 1
    async with SessionLocal() as session:
        presence = (await session.execute(sa_select(RequestConversationHourlyRollup))).scalars().all()
    assert [(row.conversation_id, row.request_count) for row in presence] == [("conv_gate", 1)]


@pytest.mark.asyncio
async def test_usage_history_protects_latest_by_recorded_at_not_insert_order(db_setup, monkeypatch):
    """Out-of-chronology inserts: the row the product treats as latest (max
    recorded_at) must survive even when a later-inserted row carries an older
    recorded_at (and a higher id)."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_ooo", "ooo@example.com"))
        # Inserted first: the chronologically-latest sample (lower id).
        session.add(
            UsageHistory(
                account_id="acc_ooo", window="primary", used_percent=55.0, recorded_at=now - timedelta(days=100)
            )
        )
        # Inserted later: an OLDER sample backfilled afterwards (higher id).
        session.add(
            UsageHistory(
                account_id="acc_ooo", window="primary", used_percent=11.0, recorded_at=now - timedelta(days=180)
            )
        )
        await session.commit()

    _set_retention(monkeypatch, usage_history=45)
    deleted = await run_retention_pass(now=now)
    assert deleted["usage_history"] == 1

    async with SessionLocal() as session:
        rows = (await session.execute(select(UsageHistory))).scalars().all()
    assert len(rows) == 1
    assert rows[0].used_percent == 55.0
