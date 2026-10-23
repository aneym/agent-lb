from __future__ import annotations

from tests.floor.account_deletion_floor_support import (
    Account,
    AccountsRepository,
    SessionLocal,
    _account_row,
    _batch_pinning_cases,
    _hourly_rows_for_dimension,
    _lifetime_rollup,
    _log_rows,
    _make_account,
    _run_one_detach_chunk,
    _seed_account,
    json,
    pytest,
    run_account_deletion_pass,
    run_fold_pass,
    run_hourly_fold_pass,
    text,
    timedelta,
    to_dimension,
    update,
    utcnow,
)


@pytest.mark.asyncio
async def test_supersede_after_partial_drain_preserves_folded_attribution(db_setup):
    """Rows drained before a supersede stay drained, and folded rollups keep
    attributing that traffic to the revived account — with no double count
    from later folds (drained below-watermark rows are never re-folded)."""
    now = utcnow()
    account_dimension = to_dimension("acc_bg_sfold")
    await _seed_account("acc_bg_sfold", log_count=2, requested_at=now - timedelta(days=5))

    # Fold the two rows under the account dimension first.
    await run_fold_pass(now=now - timedelta(days=3))
    await run_hourly_fold_pass(now=now - timedelta(days=3))
    folded_before = sum(row.request_count for row in await _hourly_rows_for_dimension(account_dimension))
    assert folded_before == 2

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_sfold")
    # One chunk detaches both already-folded rows.
    assert await _run_one_detach_chunk("acc_bg_sfold", batch_size=10) == 2

    # Re-import supersedes before finalization ever runs.
    async with SessionLocal() as session:
        saved = await AccountsRepository(session).upsert(
            _make_account("acc_bg_sfold", "acc_bg_sfold@example.com"), merge_by_email=True
        )
        assert saved.id == "acc_bg_sfold"
    assert await run_account_deletion_pass(batch_size=10) == {}

    # Folded attribution is the permanent end state: unchanged by later
    # folds (no loss, no double count), while raw rows stay detached.
    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    folded_after = sum(row.request_count for row in await _hourly_rows_for_dimension(account_dimension))
    assert folded_after == folded_before
    assert await _lifetime_rollup("acc_bg_sfold") is not None
    logs = await _log_rows("acc_bg_sfold")
    assert len(logs) == 2
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)


@pytest.mark.asyncio
async def test_supersede_between_drain_and_finalize_is_abandoned(db_setup):
    await _seed_account("acc_bg_race", log_count=2)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_race")
    assert await _run_one_detach_chunk("acc_bg_race", batch_size=10) == 2

    # Marker cleared right before finalization (replacement won the race).
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == "acc_bg_race")
            .values(delete_requested_at=None, delete_history_requested=False)
        )
        await session.commit()

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_bg_race", only_pending=True) is False
    assert await _account_row("acc_bg_race") is not None


def test_chunk_batch_statements_pin_account_leading_index_order():
    """The chunk batch shape is what keeps the planner off sequential scans.

    An ``account_id = :id LIMIT n`` subquery plans as a LIMIT-terminated Seq
    Scan on the production planner for exactly the large accounts the drain
    targets (equality folds account_id out of the sort pathkeys). The batch
    builders must keep (a) the range predicate pair (never plain equality)
    and (b) an ORDER BY that lists the target account-leading index's exact
    column order, so that index is the only sort-free plan.
    """
    from sqlalchemy.dialects import postgresql as postgresql_dialect

    for batch_fn, table, index_name in _batch_pinning_cases():
        index = next(idx for idx in table.indexes if idx.name == index_name)
        sql = str(batch_fn("acc_bg_pin", 50).compile(dialect=postgresql_dialect.dialect()))
        expected_order = ", ".join(f"{table.name}.{column.name}" for column in index.columns)
        assert f"ORDER BY {expected_order}" in sql, sql
        assert f"{table.name}.account_id >= " in sql, sql
        assert f"{table.name}.account_id <= " in sql, sql
        assert f"{table.name}.account_id = " not in sql, sql


@pytest.mark.asyncio
async def test_chunk_batch_query_plan_uses_account_leading_indexes_postgresql(db_setup):
    """The batch ORDER BY must be served by the pinned index, not a sort.

    Sequential/bitmap scans and (incremental) sorts are disabled so the
    planner has to surface an ordered index path for the batch shape; the
    only index that can provide the ORDER BY after the leading account_id
    range is the pinned account-leading index. A drained (or missing)
    account's probe must terminate on the same index instead of falling
    back to a heap scan.
    """
    await _seed_account("acc_bg_plan", log_count=8, usage_count=8)
    async with SessionLocal() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        from app.db.models import AdditionalUsageHistory

        session.add_all(
            AdditionalUsageHistory(
                account_id="acc_bg_plan",
                quota_key="codex_spark",
                limit_name="GPT-5.3-Codex-Spark",
                metered_feature="codex_bengalfox",
                window="primary",
                used_percent=float(index),
            )
            for index in range(8)
        )
        await session.commit()

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        await session.execute(text("SET enable_sort = off"))
        await session.execute(text("SET enable_incremental_sort = off"))
        for batch_fn, _table, index_name in _batch_pinning_cases():
            for account_id in ("acc_bg_plan", "acc_bg_plan_drained_probe"):
                compiled = batch_fn(account_id, 5).compile(
                    dialect=session.get_bind().dialect, compile_kwargs={"literal_binds": True}
                )
                plan = (await session.execute(text(f"EXPLAIN (FORMAT JSON) {compiled}"))).scalar_one()
                plan_json = json.dumps(plan)
                assert index_name in plan_json, (account_id, plan_json)
                assert "Seq Scan" not in plan_json, (account_id, plan_json)
                assert "Sort Key" not in plan_json, (account_id, plan_json)


@pytest.mark.asyncio
async def test_pass_probes_drained_tables_once_per_pass(db_setup, monkeypatch):
    """A table observed empty for an account is not re-probed on later rounds
    of the same pass: each probe is a full account-row-locking transaction,
    and per-account statistics can go stale exactly during the churn window."""
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_memo", log_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_memo")

    calls = {"usage_history": 0, "additional_usage_history": 0, "request_logs": 0}
    for attr, key in (
        ("_usage_history_chunk", "usage_history"),
        ("_additional_usage_history_chunk", "additional_usage_history"),
        ("_request_logs_chunk", "request_logs"),
    ):
        original = getattr(deletion, attr)

        def _make_spy(original=original, key=key):
            async def spy(session, account_id, *, delete_history, batch_size):
                calls[key] += 1
                return await original(session, account_id, delete_history=delete_history, batch_size=batch_size)

            return spy

        monkeypatch.setattr(deletion, attr, _make_spy())

    outcomes = await run_account_deletion_pass(batch_size=1)
    assert outcomes == {"acc_bg_memo": "finalized"}
    # usage tables: exactly one (empty) probe in round 1, then skipped while
    # rounds 2-4 drain the logs; request_logs: three one-row chunks plus the
    # final empty probe that lets the pass finalize.
    assert calls == {"usage_history": 1, "additional_usage_history": 1, "request_logs": 4}
    assert await _account_row("acc_bg_memo") is None
