from __future__ import annotations

from tests.floor.account_deletion_floor_support import (
    ACCOUNT_PENDING_DELETION_REASON,
    Account,
    AccountsRepository,
    AccountStatus,
    RequestLog,
    RequestLogsRepository,
    SessionLocal,
    TokenEncryptor,
    _account_row,
    _add_log,
    _attached_log_count,
    _legacy_replace_credentials,
    _log_rows,
    _make_account,
    _run_one_detach_chunk,
    _seed_account,
    func,
    pytest,
    run_account_deletion_pass,
    select,
    timedelta,
    update,
    utcnow,
)


@pytest.mark.asyncio
async def test_restart_resumes_partial_drain(db_setup):
    await _seed_account("acc_bg_resume", log_count=5, usage_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_resume")

    # Simulate a crash after one detach chunk: progress lives in the rows.
    assert await _run_one_detach_chunk("acc_bg_resume", batch_size=2) == 2
    assert await _attached_log_count("acc_bg_resume") == 3

    # A fresh pass (restarted leader) resumes from the database state.
    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_resume": "finalized"}
    assert await _account_row("acc_bg_resume") is None
    logs = await _log_rows("acc_bg_resume")
    assert len(logs) == 5
    assert all(row.account_id is None for row in logs)


@pytest.mark.asyncio
async def test_straggler_row_settled_mid_drain_is_finalized(db_setup):
    """A stream that settles its request-log row after the drain chunks ran
    is caught by finalization's residual sweep."""
    await _seed_account("acc_bg_late", log_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_late")
    assert await _run_one_detach_chunk("acc_bg_late", batch_size=10) == 3

    async with SessionLocal() as session:
        await _add_log(
            RequestLogsRepository(session),
            account_id="acc_bg_late",
            request_id="req_acc_bg_late_straggler",
            requested_at=utcnow() - timedelta(hours=1),
        )

    outcomes = await run_account_deletion_pass(batch_size=10)
    assert outcomes == {"acc_bg_late": "finalized"}
    logs = await _log_rows("acc_bg_late")
    assert len(logs) == 4
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)


@pytest.mark.asyncio
async def test_credential_replacement_supersedes_pending_deletion(db_setup):
    await _seed_account("acc_bg_super", log_count=4)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_super")
    assert await _run_one_detach_chunk("acc_bg_super", batch_size=2) == 2

    # Re-import lands on the same row via the slot-identity path and clears
    # the marker (credential replacement supersedes the deletion).
    async with SessionLocal() as session:
        replacement = _make_account("acc_bg_super", "acc_bg_super@example.com")
        saved = await AccountsRepository(session).upsert(replacement, merge_by_email=True)
        assert saved.id == "acc_bg_super"

    row = await _account_row("acc_bg_super")
    assert row is not None
    assert row.delete_requested_at is None
    assert row.status is AccountStatus.ACTIVE

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {}
    assert await _account_row("acc_bg_super") is not None
    # Rows detached before the supersede stay detached; the rest survive.
    assert await _attached_log_count("acc_bg_super") == 2


@pytest.mark.asyncio
async def test_legacy_replica_replacement_supersedes_mid_drain(db_setup):
    """A replacement handled by a pre-upgrade replica cannot clear the marker;
    fresh (non-wiped) ciphertext on a marked row must itself supersede."""
    encryptor = TokenEncryptor()
    await _seed_account("acc_bg_legacy", log_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_legacy")
    assert await _run_one_detach_chunk("acc_bg_legacy", batch_size=2) == 2

    await _legacy_replace_credentials("acc_bg_legacy", encryptor)

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_legacy": "superseded"}
    row = await _account_row("acc_bg_legacy")
    assert row is not None
    # The worker cleared the marker itself and preserved the fresh material.
    assert row.delete_requested_at is None
    assert encryptor.decrypt(row.refresh_token_encrypted) == "fresh-refresh"
    # Rows detached before the replacement stay detached; the rest survive.
    assert await _attached_log_count("acc_bg_legacy") == 1
    # The account is no longer rescanned on later passes.
    assert await run_account_deletion_pass(batch_size=2) == {}


@pytest.mark.asyncio
async def test_legacy_replacement_with_empty_refresh_token_supersedes(db_setup):
    """A legal replacement may carry an empty refresh token while providing
    fresh access/id material; a refresh-only wipe check would mistake it for
    the original wipe and finalize the freshly replaced account."""
    encryptor = TokenEncryptor()
    await _seed_account("acc_bg_legacy_er", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_legacy_er")
    assert await _run_one_detach_chunk("acc_bg_legacy_er", batch_size=10) == 1

    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == "acc_bg_legacy_er")
            .values(
                access_token_encrypted=encryptor.encrypt("fresh-access"),
                refresh_token_encrypted=encryptor.encrypt(""),
                id_token_encrypted=encryptor.encrypt("fresh-id"),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()

    outcomes = await run_account_deletion_pass(batch_size=10)
    assert outcomes == {"acc_bg_legacy_er": "superseded"}
    row = await _account_row("acc_bg_legacy_er")
    assert row is not None
    assert row.delete_requested_at is None
    assert encryptor.decrypt(row.access_token_encrypted) == "fresh-access"


@pytest.mark.asyncio
async def test_legacy_replica_replacement_before_finalize_is_abandoned(db_setup):
    encryptor = TokenEncryptor()
    await _seed_account("acc_bg_legacy_fin", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_legacy_fin")
    assert await _run_one_detach_chunk("acc_bg_legacy_fin", batch_size=10) == 1

    await _legacy_replace_credentials("acc_bg_legacy_fin", encryptor)

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_bg_legacy_fin", only_pending=True) is False
    row = await _account_row("acc_bg_legacy_fin")
    assert row is not None
    assert row.delete_requested_at is None
    assert encryptor.decrypt(row.refresh_token_encrypted) == "fresh-refresh"


@pytest.mark.asyncio
async def test_repeat_delete_short_circuits_without_waiting_on_chunk_lock(db_setup):
    """A repeat DELETE must keep the millisecond contract even while a drain
    chunk transaction holds the account row lock."""
    import asyncio

    async with SessionLocal() as probe:
        if probe.get_bind().dialect.name != "postgresql":
            pytest.skip("row-lock wait behavior is PostgreSQL-specific")

    await _seed_account("acc_bg_repeat", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_repeat")

    async with SessionLocal() as locker:
        # Hold the same lock a drain chunk holds for its whole transaction.
        await locker.execute(select(Account.id).where(Account.id == "acc_bg_repeat").with_for_update(key_share=True))
        async with SessionLocal() as session:
            repeat = await asyncio.wait_for(AccountsRepository(session).begin_delete("acc_bg_repeat"), timeout=2.0)
        assert repeat is True
        await locker.rollback()


@pytest.mark.asyncio
async def test_chunk_self_heals_drift_from_unfenced_replicas(db_setup):
    """During a rolling deploy, pre-upgrade replicas' unfenced writers can
    replace the terminal status or recreate API-key assignments on a marked
    row; the next chunk transaction must re-fence both."""
    from app.db.models import ApiKey, ApiKeyAccountAssignment
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_heal", log_count=2)
    async with SessionLocal() as session:
        session.add(
            ApiKey(
                id="key_bg_heal",
                name="heal-key",
                key_hash="hash_bg_heal",
                key_prefix="sk-heal",
                account_assignment_scope_enabled=True,
            )
        )
        await session.commit()
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_heal")

    # Old-replica drift: unfenced status write + unconditional assignment
    # insert (tokens stay wiped, so this is NOT a credential replacement).
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == "acc_bg_heal")
            .values(status=AccountStatus.RATE_LIMITED, deactivation_reason="rate_limited")
        )
        session.add(ApiKeyAccountAssignment(api_key_id="key_bg_heal", account_id="acc_bg_heal"))
        await session.commit()

    affected = await deletion._run_chunk(deletion._usage_history_chunk, "acc_bg_heal", batch_size=10)
    assert affected is not None

    row = await _account_row("acc_bg_heal")
    assert row is not None
    assert row.status is AccountStatus.DEACTIVATED
    assert row.deactivation_reason == ACCOUNT_PENDING_DELETION_REASON
    assert row.delete_requested_at is not None
    async with SessionLocal() as session:
        assigned = (
            await session.execute(
                select(func.count())
                .select_from(ApiKeyAccountAssignment)
                .where(ApiKeyAccountAssignment.account_id == "acc_bg_heal")
            )
        ).scalar_one()
    assert assigned == 0


@pytest.mark.asyncio
async def test_finalization_serializes_against_inflight_log_insert(db_setup):
    """PostgreSQL: an in-flight stream's request-log insert holds the FK KEY
    SHARE on the account row; finalization's FOR UPDATE row upgrade must wait
    for it, so the late row is swept instead of surviving as a live orphan
    via ON DELETE SET NULL."""
    import asyncio

    async with SessionLocal() as probe:
        if probe.get_bind().dialect.name != "postgresql":
            pytest.skip("FK KEY SHARE / FOR UPDATE interleaving is PostgreSQL-specific")

    await _seed_account("acc_bg_inflight", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_inflight")
    assert await _run_one_detach_chunk("acc_bg_inflight", batch_size=10) == 1

    async with SessionLocal() as inflight:
        # In-flight stream: the insert takes (and holds) FK KEY SHARE on the
        # account row until commit.
        inflight.add(
            RequestLog(
                account_id="acc_bg_inflight",
                request_id="req_acc_bg_inflight_late",
                requested_at=utcnow(),
                model="gpt-5.1-codex",
                status="success",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
            )
        )
        await inflight.flush()

        pass_task = asyncio.create_task(run_account_deletion_pass(batch_size=10))
        # Finalization must block on the row upgrade while the insert is open.
        done, _ = await asyncio.wait({pass_task}, timeout=1.0)
        commit_first = not done
        await inflight.commit()
        outcomes = await pass_task

    assert commit_first, "finalization finished while an uncommitted FK insert held KEY SHARE"
    assert outcomes == {"acc_bg_inflight": "finalized"}
    assert await _account_row("acc_bg_inflight") is None
    logs = await _log_rows("acc_bg_inflight")
    assert len(logs) == 2
    # The late row was swept by the residual sweep, not orphaned live.
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)


@pytest.mark.asyncio
async def test_assignment_insert_rechecks_marker_atomically(db_setup):
    """replace_account_assignments must skip marked accounts even when an
    earlier validation (different transaction) still believed they existed."""
    from app.db.models import ApiKey, ApiKeyAccountAssignment
    from app.modules.api_keys.repository import ApiKeysRepository

    await _seed_account("acc_bg_atomic", log_count=0)
    async with SessionLocal() as session:
        session.add(
            ApiKey(
                id="key_bg_atomic",
                name="atomic-recheck-key",
                key_hash="hash_bg_atomic",
                key_prefix="sk-atomic",
                account_assignment_scope_enabled=True,
            )
        )
        await session.commit()

    # DELETE lands after validation would have passed.
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_atomic")

    async with SessionLocal() as session:
        await ApiKeysRepository(session).replace_account_assignments("key_bg_atomic", ["acc_bg_atomic"])

    async with SessionLocal() as session:
        assigned = (
            await session.execute(
                select(func.count())
                .select_from(ApiKeyAccountAssignment)
                .where(ApiKeyAccountAssignment.api_key_id == "key_bg_atomic")
            )
        ).scalar_one()
    assert assigned == 0


@pytest.mark.asyncio
async def test_marked_account_cannot_be_assigned_to_api_key(async_client, db_setup):
    await _seed_account("acc_bg_assign", log_count=1)

    delete = await async_client.delete("/api/accounts/acc_bg_assign")
    assert delete.status_code == 200

    # A key update racing (or following) the DELETE must not recreate an
    # assignment that would re-surface the deleted account in key listings.
    create = await async_client.post("/api/api-keys/", json={"name": "post-delete-key"})
    assert create.status_code == 200
    key_id = create.json()["id"]
    update_resp = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"assignedAccountIds": ["acc_bg_assign"]},
    )
    assert update_resp.status_code == 400
    assert update_resp.json()["error"]["code"] == "invalid_api_key_payload"
