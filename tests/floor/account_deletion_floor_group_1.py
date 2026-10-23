from __future__ import annotations

from tests.floor.account_deletion_floor_support import (
    _ORPHAN_DIMENSION,
    ACCOUNT_PENDING_DELETION_REASON,
    AccountsRepository,
    AccountStatus,
    RequestLogsRepository,
    SessionLocal,
    StickySession,
    StickySessionKind,
    TokenEncryptor,
    UsageHistory,
    _account_row,
    _add_log,
    _attached_log_count,
    _demand_rows_for_dimension,
    _fake_id_token,
    _hourly_rows_for_dimension,
    _lifetime_rollup,
    _log_rows,
    _make_account,
    _run_one_detach_chunk,
    _seed_account,
    func,
    pytest,
    run_account_deletion_pass,
    run_fold_pass,
    run_hourly_fold_pass,
    select,
    timedelta,
    to_dimension,
    utcnow,
)


@pytest.mark.asyncio
async def test_delete_api_marks_and_hides_immediately(async_client, db_setup):
    await _seed_account("acc_bg_mark", log_count=2, usage_count=2)

    delete = await async_client.delete("/api/accounts/acc_bg_mark")
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    # Hidden from the listing immediately, before any background work ran.
    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    assert all(entry["accountId"] != "acc_bg_mark" for entry in accounts.json()["accounts"])

    # The row itself survives, terminal and marked, until the worker drains it.
    row = await _account_row("acc_bg_mark")
    assert row is not None
    assert row.status is AccountStatus.DEACTIVATED
    assert row.deactivation_reason == ACCOUNT_PENDING_DELETION_REASON
    assert row.delete_requested_at is not None
    assert await _attached_log_count("acc_bg_mark") == 2

    # Repeat request is idempotent and does not escalate the frozen variant.
    repeat = await async_client.delete("/api/accounts/acc_bg_mark?delete_history=true")
    assert repeat.status_code == 200
    row = await _account_row("acc_bg_mark")
    assert row is not None
    assert row.delete_history_requested is False

    # A marked account is gone from the operator's perspective: reactivation
    # reports not-found instead of racing the deletion worker.
    reactivate = await async_client.post("/api/accounts/acc_bg_mark/reactivate")
    assert reactivate.status_code == 404

    # Credential exports must not keep serving decrypted tokens during the
    # drain window: the synchronous delete 404'd here immediately.
    for export_path in ("export", "export/auth", "export/opencode-auth"):
        export = await async_client.post(f"/api/accounts/acc_bg_mark/{export_path}")
        assert export.status_code == 404, export_path

    # Every other ID-based account route treats the marked row as gone too —
    # the synchronous delete returned 404 on all of them once the row was
    # removed.
    base = "/api/accounts/acc_bg_mark"
    assert (await async_client.get(f"{base}/trends")).status_code == 404
    assert (await async_client.get(f"{base}/usage-reset-credits")).status_code == 404
    assert (await async_client.post(f"{base}/usage-reset-credits/consume")).status_code == 404
    assert (await async_client.post(f"{base}/probe")).status_code == 404
    assert (await async_client.post(f"{base}/pause")).status_code == 404
    assert (await async_client.patch(base, json={"securityWorkAuthorized": True})).status_code == 404
    assert (await async_client.put(f"{base}/alias", json={"alias": "ghost"})).status_code == 404
    assert (await async_client.put(f"{base}/limit-warmup", json={"enabled": True})).status_code == 404
    assert (await async_client.put(f"{base}/routing-policy", json={"routingPolicy": "preserve"})).status_code == 404


@pytest.mark.asyncio
async def test_begin_delete_preserves_seat_identity_before_token_wipe(db_setup):
    """Legacy rows carry their seat identity only inside the id-token claims;
    targeted reauthentication (a supersede path) verifies the seat against
    chatgpt_user_id or those claims, so the wipe must backfill the non-secret
    identity first."""
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = _make_account("acc_bg_seat", "acc_bg_seat@example.com")
        assert account.chatgpt_user_id is None
        account.id_token_encrypted = encryptor.encrypt(_fake_id_token({"sub": "user-legacy-seat"}))
        await AccountsRepository(session).upsert(account)

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_seat")

    row = await _account_row("acc_bg_seat")
    assert row is not None
    # The ciphertext is wiped (no usable credentials remain on the row)...
    assert encryptor.decrypt(row.id_token_encrypted) == ""
    # ...but the seat identity survives in the non-secret column.
    assert row.chatgpt_user_id == "user-legacy-seat"


@pytest.mark.asyncio
async def test_marked_account_wipes_tokens_and_rejects_stale_status_writes(db_setup):
    await _seed_account("acc_bg_fence", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_fence")

    # The surviving row must not carry usable credentials: readers that do
    # not know the marker (pre-upgrade replicas during a rolling deploy) can
    # only produce empty credentials from it.
    row = await _account_row("acc_bg_fence")
    assert row is not None
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(row.access_token_encrypted) == ""
    assert encryptor.decrypt(row.refresh_token_encrypted) == ""
    assert encryptor.decrypt(row.id_token_encrypted) == ""

    # Stale in-flight settlements (e.g. a late 429 for a request selected
    # before the DELETE) must not replace the terminal state and make the
    # account selectable again mid-drain.
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        assert await repo.update_status("acc_bg_fence", AccountStatus.RATE_LIMITED, "rate_limited") is False
        assert (
            await repo.update_status_if_current(
                "acc_bg_fence",
                AccountStatus.RATE_LIMITED,
                "rate_limited",
                expected_status=AccountStatus.DEACTIVATED,
                expected_deactivation_reason=ACCOUNT_PENDING_DELETION_REASON,
            )
            is False
        )
    row = await _account_row("acc_bg_fence")
    assert row is not None
    assert row.status is AccountStatus.DEACTIVATED
    assert row.deactivation_reason == ACCOUNT_PENDING_DELETION_REASON
    assert row.delete_requested_at is not None


@pytest.mark.asyncio
async def test_chunked_soft_delete_drains_across_chunk_boundaries(db_setup):
    await _seed_account("acc_bg_soft", log_count=7, usage_count=5)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key="sticky_bg_soft",
                kind=StickySessionKind.CODEX_SESSION,
                account_id="acc_bg_soft",
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_soft")

    outcomes = await run_account_deletion_pass(batch_size=3)
    assert outcomes == {"acc_bg_soft": "finalized"}

    assert await _account_row("acc_bg_soft") is None
    logs = await _log_rows("acc_bg_soft")
    assert len(logs) == 7
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)
    async with SessionLocal() as session:
        usage_left = (
            await session.execute(select(func.count(UsageHistory.id)).where(UsageHistory.account_id == "acc_bg_soft"))
        ).scalar_one()
        sticky_left = (
            await session.execute(
                select(func.count(StickySession.key)).where(StickySession.account_id == "acc_bg_soft")
            )
        ).scalar_one()
    assert usage_left == 0
    assert sticky_left == 0
    assert await _lifetime_rollup("acc_bg_soft") is None

    # Idempotent: a second pass finds nothing to do.
    assert await run_account_deletion_pass(batch_size=3) == {}


@pytest.mark.asyncio
async def test_chunked_hard_delete_removes_history(db_setup):
    await _seed_account("acc_bg_hard", log_count=5, usage_count=2)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_hard", delete_history=True)

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_hard": "finalized"}

    assert await _account_row("acc_bg_hard") is None
    assert await _log_rows("acc_bg_hard") == []


@pytest.mark.asyncio
async def test_fold_interleaved_between_chunks_does_not_resurrect_soft(db_setup):
    """A fold slice committing between detach chunks re-attributes still-
    attached rows to the account; finalization's fold-locked mirrors must
    move ALL of it to the orphaned-deleted dimension."""
    now = utcnow()
    account_dimension = to_dimension("acc_bg_fold")
    # Group A (2 rows) old enough for the first fold; group B (2 rows) folded
    # only by the interleaved fold below.
    await _seed_account("acc_bg_fold", log_count=2, requested_at=now - timedelta(days=5))
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        for index in range(2):
            await _add_log(
                logs_repo,
                account_id="acc_bg_fold",
                request_id=f"req_acc_bg_fold_b{index}",
                requested_at=now - timedelta(days=2),
            )

    # First fold covers only group A (target = now-3d - FOLD_LAG).
    await run_fold_pass(now=now - timedelta(days=3))
    await run_hourly_fold_pass(now=now - timedelta(days=3))
    assert await _hourly_rows_for_dimension(account_dimension) != []

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_fold")

    # One chunk detaches the two oldest (group A) rows; group B stays attached.
    assert await _run_one_detach_chunk("acc_bg_fold", batch_size=2) == 2
    assert await _attached_log_count("acc_bg_fold") == 2

    # Interleaved folds aggregate group B while it is still attributed.
    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    assert await _lifetime_rollup("acc_bg_fold") is not None
    interleaved_hourly = await _hourly_rows_for_dimension(account_dimension)
    assert sum(row.request_count for row in interleaved_hourly if not row.is_deleted) >= 2

    # Resume and finish the deletion.
    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_fold": "finalized"}

    # No folded row anywhere still carries the account dimension...
    assert await _hourly_rows_for_dimension(account_dimension) == []
    assert await _demand_rows_for_dimension(account_dimension) == []
    assert await _lifetime_rollup("acc_bg_fold") is None
    # ...and the orphaned-deleted dimension preserves the full folded history.
    orphan_hourly = await _hourly_rows_for_dimension(_ORPHAN_DIMENSION)
    assert sum(row.request_count for row in orphan_hourly if row.is_deleted) == 4
    logs = await _log_rows("acc_bg_fold")
    assert len(logs) == 4
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)

    # Folds after finalization see only detached raw rows: nothing new may
    # appear under the account dimension.
    await run_hourly_fold_pass(now=now + timedelta(days=1))
    await run_fold_pass(now=now + timedelta(days=1))
    assert await _hourly_rows_for_dimension(account_dimension) == []
    assert await _lifetime_rollup("acc_bg_fold") is None


@pytest.mark.asyncio
async def test_fold_interleaved_between_chunks_does_not_resurrect_hard(db_setup):
    now = utcnow()
    account_dimension = to_dimension("acc_bg_fhard")
    await _seed_account("acc_bg_fhard", log_count=2, requested_at=now - timedelta(days=5))
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        for index in range(2):
            await _add_log(
                logs_repo,
                account_id="acc_bg_fhard",
                request_id=f"req_acc_bg_fhard_b{index}",
                requested_at=now - timedelta(days=2),
            )
    await run_hourly_fold_pass(now=now - timedelta(days=3))

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_fhard", delete_history=True)

    assert await _run_one_detach_chunk("acc_bg_fhard", batch_size=2, delete_history=True) == 2
    await run_hourly_fold_pass(now=now)

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_fhard": "finalized"}

    assert await _hourly_rows_for_dimension(account_dimension) == []
    assert await _demand_rows_for_dimension(account_dimension) == []
    assert await _log_rows("acc_bg_fhard") == []


@pytest.mark.asyncio
async def test_pass_round_robins_chunks_across_pending_accounts(db_setup, monkeypatch):
    """One account's long drain must not starve another: each round advances
    every pending account by at most one full chunk."""
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_rr_a", log_count=3)
    await _seed_account("acc_bg_rr_b", log_count=3)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        assert await repo.begin_delete("acc_bg_rr_a")
        assert await repo.begin_delete("acc_bg_rr_b")

    chunk_calls: list[str] = []
    original_chunk = deletion._request_logs_chunk

    async def spy_chunk(session, account_id, *, delete_history, batch_size):
        chunk_calls.append(account_id)
        return await original_chunk(session, account_id, delete_history=delete_history, batch_size=batch_size)

    monkeypatch.setattr(deletion, "_request_logs_chunk", spy_chunk)

    outcomes = await run_account_deletion_pass(batch_size=1)
    assert outcomes == {"acc_bg_rr_a": "finalized", "acc_bg_rr_b": "finalized"}
    # Full chunks alternate between the two accounts instead of draining one
    # account to completion first.
    assert chunk_calls[:6] == [
        "acc_bg_rr_a",
        "acc_bg_rr_b",
        "acc_bg_rr_a",
        "acc_bg_rr_b",
        "acc_bg_rr_a",
        "acc_bg_rr_b",
    ]
    assert await _account_row("acc_bg_rr_a") is None
    assert await _account_row("acc_bg_rr_b") is None


@pytest.mark.asyncio
async def test_pass_picks_up_account_marked_mid_pass(db_setup, monkeypatch):
    """A DELETE that lands while a pass is draining another account is picked
    up by the between-rounds re-scan, not deferred to the next tick."""
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_mid_a", log_count=2)
    await _seed_account("acc_bg_mid_b", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_mid_a")

    original_advance = deletion._advance_account
    marked_second = False

    async def advance_and_mark(account_id, *, batch_size, drained=None):
        nonlocal marked_second
        if not marked_second:
            marked_second = True
            async with SessionLocal() as session:
                assert await AccountsRepository(session).begin_delete("acc_bg_mid_b")
        return await original_advance(account_id, batch_size=batch_size, drained=drained)

    monkeypatch.setattr(deletion, "_advance_account", advance_and_mark)

    outcomes = await run_account_deletion_pass(batch_size=1)
    assert outcomes == {"acc_bg_mid_a": "finalized", "acc_bg_mid_b": "finalized"}
    assert await _account_row("acc_bg_mid_a") is None
    assert await _account_row("acc_bg_mid_b") is None
