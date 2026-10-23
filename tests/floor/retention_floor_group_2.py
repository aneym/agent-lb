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
    _make_account,
    _set_dashboard_retention,
    _set_retention,
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


@pytest.mark.asyncio
async def test_usage_history_pruning_null_window_and_multi_identity(db_setup, monkeypatch):
    """NULL and 'primary' windows are ONE identity (coalesce); distinct
    windows and quota keys are separate identities, each keeping its latest."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_multi", "multi@example.com"))
        # NULL window (legacy primary) older than the 'primary' row: same
        # identity, so only the newer 'primary' row survives.
        session.add(
            UsageHistory(account_id="acc_multi", window=None, used_percent=1.0, recorded_at=now - timedelta(days=200))
        )
        session.add(
            UsageHistory(
                account_id="acc_multi", window="primary", used_percent=2.0, recorded_at=now - timedelta(days=150)
            )
        )
        # Secondary window is its own identity; its single old row survives.
        session.add(
            UsageHistory(
                account_id="acc_multi", window="secondary", used_percent=3.0, recorded_at=now - timedelta(days=180)
            )
        )
        # Two quota keys: each keeps its own latest.
        for quota_key, percents in (("gpt-5-pro", (10.0, 11.0)), ("sora-video", (20.0, 21.0))):
            for offset, percent in enumerate(percents):
                session.add(
                    AdditionalUsageHistory(
                        account_id="acc_multi",
                        quota_key=quota_key,
                        limit_name=quota_key,
                        metered_feature=quota_key,
                        window="primary",
                        used_percent=percent,
                        recorded_at=now - timedelta(days=200 - offset * 10),
                    )
                )
        await session.commit()

    _set_retention(monkeypatch, usage_history=45)
    deleted = await run_retention_pass(now=now)
    assert deleted["usage_history"] == 1  # only the NULL-window row
    assert deleted["additional_usage_history"] == 2  # older row of each quota key

    async with SessionLocal() as session:
        usage_rows = (await session.execute(select(UsageHistory))).scalars().all()
        additional_rows = (await session.execute(select(AdditionalUsageHistory))).scalars().all()
    assert sorted((row.window, row.used_percent) for row in usage_rows) == [("primary", 2.0), ("secondary", 3.0)]
    assert sorted((row.quota_key, row.used_percent) for row in additional_rows) == [
        ("gpt-5-pro", 11.0),
        ("sora-video", 21.0),
    ]


@pytest.mark.asyncio
async def test_usage_history_backlog_drains_across_batches(db_setup, monkeypatch):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_ubatch", "ubatch@example.com"))
        for index in range(7):
            session.add(
                UsageHistory(
                    account_id="acc_ubatch",
                    window="primary",
                    used_percent=float(index),
                    recorded_at=now - timedelta(days=200 - index),
                )
            )
        await session.commit()

    _set_retention(monkeypatch, usage_history=45)
    monkeypatch.setattr(retention_job, "BATCH_SIZE", 2)
    deleted = await run_retention_pass(now=now)
    assert deleted["usage_history"] == 6  # all but the latest row

    async with SessionLocal() as session:
        rows = (await session.execute(select(UsageHistory))).scalars().all()
    assert len(rows) == 1
    assert rows[0].used_percent == 6.0


def test_retention_settings_reject_absurd_values():
    with pytest.raises(ValidationError):
        Settings(request_log_retention_days=100_000_000)
    with pytest.raises(ValidationError):
        Settings(usage_history_retention_days=1_000_000)


@pytest.mark.asyncio
async def test_api_key_totals_survive_pruning_and_match_pre_fold(db_setup, monkeypatch):
    """Per-key lifetime summaries fold alongside account sums; pruning folded
    rows must not change them, and folded totals must equal pre-fold ones."""
    from app.db.models import ApiKey, ApiKeyUsageRollup
    from app.modules.api_keys.repository import ApiKeysRepository

    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_key", "key@example.com"))
        session.add(ApiKey(id="key_ret", name="Retained", key_hash="hash_ret", key_prefix="sk-ret"))
        await session.commit()
        for index, days_ago in enumerate((60, 40, 1)):
            log = await logs_repo.add_log(
                account_id="acc_key",
                request_id=f"req_key_{index}",
                model="gpt-5.1-codex",
                input_tokens=100,
                output_tokens=50,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now - timedelta(days=days_ago),
                cost_usd=0.01,
                api_key_id="key_ret",
            )
            assert log is not None

    async def _key_summary():
        async with SessionLocal() as session:
            return await ApiKeysRepository(session).get_usage_summary_by_key_id("key_ret")

    before = await _key_summary()
    assert before.request_count == 3
    assert before.total_tokens == 3 * 150

    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    await run_conversation_fold_pass(now=now)
    assert await _key_summary() == before

    _set_retention(monkeypatch, request_logs=30)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 2
    assert await _key_summary() == before

    # Key deletion removes its rollup row.
    async with SessionLocal() as session:
        assert await ApiKeysRepository(session).delete("key_ret")
    async with SessionLocal() as session:
        assert (await session.execute(select(ApiKeyUsageRollup))).scalars().all() == []


def test_api_key_rollup_migration_resets_prior_fold_state(tmp_path):
    """Installs that folded account rollups before this migration must be
    reset (rollup rows + watermark together) so per-key totals re-backfill
    instead of collapsing to the live tail."""
    import sqlalchemy as sa
    from alembic import command

    from app.db.migrate import _build_alembic_config

    db_path = tmp_path / "mig-key-rollup.sqlite3"
    url = f"sqlite:///{db_path}"
    cfg = _build_alembic_config(url)

    command.upgrade(cfg, "20260712_010000_add_account_usage_rollups")

    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO accounts (id, email, plan_type, access_token_encrypted, refresh_token_encrypted,"
                " id_token_encrypted, last_refresh, status, codex_installation_id)"
                " VALUES ('acc_mig', 'mig@example.com', 'plus', X'00', X'00', X'00', '2026-01-01 00:00:00',"
                " 'active', 'inst_mig')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO account_usage_rollups (account_id, request_count, input_tokens, output_tokens,"
                " cached_input_tokens, total_cost_usd) VALUES ('acc_mig', 5, 100, 50, 0, 1.0)"
            )
        )
        conn.execute(sa.text("UPDATE account_usage_rollup_state SET folded_through = '2026-07-01 00:00:00'"))

    command.upgrade(cfg, "20260712_020000_add_api_key_usage_rollups")

    with engine.begin() as conn:
        rollups = conn.execute(sa.text("SELECT count(*) FROM account_usage_rollups")).scalar_one()
        watermark = conn.execute(sa.text("SELECT folded_through FROM account_usage_rollup_state")).scalar_one()
        key_rollups = conn.execute(sa.text("SELECT count(*) FROM api_key_usage_rollups")).scalar_one()
    engine.dispose()

    assert rollups == 0
    assert str(watermark).startswith("1970-01-01")
    assert key_rollups == 0


@pytest.mark.asyncio
async def test_fold_start_covers_key_only_soft_deleted_history(db_setup):
    """A soft-deleted row with an api_key_id is invisible to the account
    aggregate but still counts for per-key totals; the backfill start must
    not skip past it."""
    from datetime import timedelta as _td

    from app.db.models import ApiKey, ApiKeyUsageRollup
    from app.db.models import RequestLog as RequestLogModel

    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_keyonly", "keyonly@example.com"))
        session.add(ApiKey(id="key_only", name="KeyOnly", key_hash="hash_keyonly", key_prefix="sk-ko"))
        # Oldest row: soft-deleted (account detached) but key-attributed —
        # only the key aggregate counts it.
        session.add(
            RequestLogModel(
                account_id=None,
                api_key_id="key_only",
                request_id="req_keyonly_old",
                model="gpt-5.1-codex",
                status="success",
                input_tokens=100,
                output_tokens=50,
                requested_at=now - timedelta(days=40),
                deleted_at=now - timedelta(days=30),
            )
        )
        await session.commit()
        await _add_log(
            logs_repo, account_id="acc_keyonly", request_id="req_keyonly_new", requested_at=now - _td(days=2)
        )

    await run_fold_pass(now=now)

    async with SessionLocal() as session:
        key_rollup = (await session.execute(select(ApiKeyUsageRollup))).scalars().all()
    assert len(key_rollup) == 1
    assert key_rollup[0].request_count == 1  # the soft-deleted key row folded

    from app.modules.api_keys.repository import ApiKeysRepository

    async with SessionLocal() as session:
        summary = await ApiKeysRepository(session).get_usage_summary_by_key_id("key_only")
    assert summary.request_count == 1
    assert summary.total_tokens == 150


@pytest.mark.asyncio
async def test_dashboard_retention_overrides_env_alias(async_client, db_setup, monkeypatch):
    """A non-NULL dashboard value wins over the deprecated env alias."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_dash", "dash@example.com"))
        await _add_log(logs_repo, account_id="acc_dash", request_id="req_40d", requested_at=now - timedelta(days=40))
        await _add_log(logs_repo, account_id="acc_dash", request_id="req_1d", requested_at=now - timedelta(days=1))

    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    await run_conversation_fold_pass(now=now)

    # Env alias alone (90 days) would keep the 40-day-old row...
    _set_retention(monkeypatch, request_logs=90)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 0

    # ...but a 30-day dashboard override prunes it without touching env.
    await _set_dashboard_retention(request_logs=30)
    deleted = await run_retention_pass(now=now)
    assert deleted["request_logs"] == 1
    async with SessionLocal() as session:
        remaining = (await session.execute(select(RequestLog.request_id))).scalars().all()
    assert sorted(remaining) == ["req_1d"]


@pytest.mark.asyncio
async def test_dashboard_zero_disables_retention_despite_env_alias(db_setup, monkeypatch):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_zero", "zero@example.com"))
        session.add(UsageHistory(account_id="acc_zero", used_percent=10.0, recorded_at=now - timedelta(days=400)))
        session.add(UsageHistory(account_id="acc_zero", used_percent=20.0, recorded_at=now - timedelta(days=390)))
        await session.commit()

    _set_retention(monkeypatch, usage_history=45)
    await _set_dashboard_retention(usage_history=0)

    deleted = await run_retention_pass(now=now)
    assert deleted == {"request_logs": 0, "usage_history": 0, "additional_usage_history": 0}
    async with SessionLocal() as session:
        assert len((await session.execute(select(UsageHistory.id))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_env_alias_applies_while_dashboard_value_unset(db_setup, monkeypatch):
    """NULL dashboard values inherit the deprecated env alias unchanged."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_env", "env@example.com"))
        session.add(UsageHistory(account_id="acc_env", used_percent=10.0, recorded_at=now - timedelta(days=400)))
        session.add(UsageHistory(account_id="acc_env", used_percent=20.0, recorded_at=now - timedelta(days=1)))
        await session.commit()

    # Force row creation so the dashboard columns exist and stay NULL.
    await _set_dashboard_retention(request_logs=None, usage_history=None)

    _set_retention(monkeypatch, usage_history=45)
    deleted = await run_retention_pass(now=now)
    assert deleted["usage_history"] == 1
    async with SessionLocal() as session:
        remaining = (await session.execute(select(UsageHistory.used_percent))).scalars().all()
    assert remaining == [20.0]
