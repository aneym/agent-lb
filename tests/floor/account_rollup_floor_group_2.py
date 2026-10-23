from __future__ import annotations

from tests.floor.account_rollup_floor_support import (
    AccountsRepository,
    RequestLogsRepository,
    SessionLocal,
    _add_log,
    _make_account,
    _summaries,
    pytest,
    timedelta,
    utcnow,
)


@pytest.mark.asyncio
async def test_summary_cache_serves_within_ttl_per_signature(db_setup, monkeypatch):
    import app.modules.accounts.repository as accounts_repository_module

    monkeypatch.setattr(accounts_repository_module, "_SUMMARY_CACHE_TTL_SECONDS", 30.0)
    accounts_repository_module._clear_request_usage_summary_cache()
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_ttl", "ttl@example.com"))
        await _add_log(logs_repo, account_id="acc_ttl", request_id="req_ttl_1", requested_at=now - timedelta(minutes=5))

    first = await _summaries()
    assert first["acc_ttl"].request_count == 1

    async with SessionLocal() as session:
        await _add_log(
            RequestLogsRepository(session),
            account_id="acc_ttl",
            request_id="req_ttl_2",
            requested_at=now - timedelta(minutes=1),
        )

    # Same signature within the TTL: served from cache, staleness tolerated.
    assert (await _summaries())["acc_ttl"].request_count == 1
    # A different account-id signature is a different cache entry.
    scoped = await _summaries(["acc_ttl"])
    assert scoped["acc_ttl"].request_count == 2

    accounts_repository_module._clear_request_usage_summary_cache()
    assert (await _summaries())["acc_ttl"].request_count == 2


@pytest.mark.asyncio
async def test_summary_cache_cleared_on_account_delete(db_setup, monkeypatch):
    import app.modules.accounts.repository as accounts_repository_module

    monkeypatch.setattr(accounts_repository_module, "_SUMMARY_CACHE_TTL_SECONDS", 30.0)
    accounts_repository_module._clear_request_usage_summary_cache()
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_keep", "keep@example.com"))
        await accounts_repo.upsert(_make_account("acc_gone", "gone@example.com"))
        await _add_log(logs_repo, account_id="acc_keep", request_id="req_keep", requested_at=now - timedelta(minutes=5))
        await _add_log(logs_repo, account_id="acc_gone", request_id="req_gone", requested_at=now - timedelta(minutes=5))

    first = await _summaries()
    assert "acc_gone" in first

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_gone")

    after = await _summaries()
    assert "acc_gone" not in after
    assert after["acc_keep"].request_count == 1


@pytest.mark.asyncio
async def test_summary_cache_cleared_on_identity_consolidation(db_setup, monkeypatch):
    import app.modules.accounts.repository as accounts_repository_module

    monkeypatch.setattr(accounts_repository_module, "_SUMMARY_CACHE_TTL_SECONDS", 30.0)
    accounts_repository_module._clear_request_usage_summary_cache()
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        canonical = _make_account("acc_cc", "cc@example.com", chatgpt_account_id="chatgpt_cc")
        duplicate = _make_account("acc_cc__copy", "cc@example.com", chatgpt_account_id="chatgpt_cc")
        await accounts_repo.upsert(canonical, merge_by_email=False)
        await accounts_repo.upsert(duplicate, merge_by_email=False)
        await _add_log(logs_repo, account_id="acc_cc", request_id="req_cc_1", requested_at=now - timedelta(minutes=5))
        await _add_log(
            logs_repo, account_id="acc_cc__copy", request_id="req_cc_2", requested_at=now - timedelta(minutes=5)
        )

    first = await _summaries()
    assert first["acc_cc"].request_count == 1
    assert first["acc_cc__copy"].request_count == 1

    async with SessionLocal() as session:
        reauth = _make_account("acc_cc", "cc@example.com", chatgpt_account_id="chatgpt_cc")
        saved = await AccountsRepository(session).upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)
        assert saved.id == "acc_cc"

    after = await _summaries()
    assert "acc_cc__copy" not in after
    assert after["acc_cc"].request_count == 2


@pytest.mark.asyncio
async def test_summary_cache_fill_discarded_when_invalidated_mid_flight(db_setup, monkeypatch):
    """A fill already computing when deletion/consolidation clears the cache
    must not re-populate it with its pre-clear result (generation fence)."""
    import app.modules.accounts.repository as accounts_repository_module

    monkeypatch.setattr(accounts_repository_module, "_SUMMARY_CACHE_TTL_SECONDS", 30.0)
    accounts_repository_module._clear_request_usage_summary_cache()
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_racefill", "racefill@example.com"))
        await _add_log(
            logs_repo, account_id="acc_racefill", request_id="req_rf", requested_at=now - timedelta(minutes=5)
        )

    real_read_state = accounts_repository_module.AccountUsageRollupRepository.read_state

    async def _read_state_with_racing_clear(self, account_ids=None):
        result = await real_read_state(self, account_ids)
        # Simulate a consolidation/deletion committing and clearing while
        # this fill is still between its two statements.
        accounts_repository_module._clear_request_usage_summary_cache()
        return result

    monkeypatch.setattr(
        accounts_repository_module.AccountUsageRollupRepository, "read_state", _read_state_with_racing_clear
    )
    first = await _summaries()
    assert first["acc_racefill"].request_count == 1
    # The interleaved clear must have won: nothing was cached.
    assert accounts_repository_module._request_usage_summary_cache == {}
