from __future__ import annotations

import pytest

from app.modules.accounts.refresh_claims import RefreshClaimCoordinator
from tests.floor.token_refresh_claims_support import (
    _create_account,
)


@pytest.mark.asyncio
async def test_claim_coordinator_win_lose_release_semantics(db_setup):
    account_id = "acc_claim_semantics"
    await _create_account(account_id)
    coordinator_a = RefreshClaimCoordinator(claimant_id="replica-a")
    coordinator_b = RefreshClaimCoordinator(claimant_id="replica-b")
    owner = "fingerprint-1"

    assert await coordinator_a.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    # Re-entrant for the same claimant AND owner, exclusive against others.
    assert await coordinator_a.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is False

    # A foreign release is a no-op; the owner's release frees the claim.
    await coordinator_b.release(account_id, owner=owner)
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is False
    await coordinator_a.release(account_id, owner=owner)
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    snapshot = await coordinator_b.current_claim(account_id)
    assert snapshot is not None
    assert snapshot.claimed_by.startswith("replica-b")
    await coordinator_b.release(account_id, owner=owner)


@pytest.mark.asyncio
async def test_claim_owner_is_per_refresh_not_process_wide(db_setup):
    """Regression: two refreshes for one account in ONE process with different
    token fingerprints must contend for the claim, not piggyback. Before the
    per-refresh owner fix the claim was keyed process-wide (account only), so
    the second owner re-entered the first owner's live claim and either
    release() deleted the other's claim, letting a third replica in mid-exchange."""
    account_id = "acc_claim_per_owner"
    await _create_account(account_id)
    # One process => one claimant identity, two distinct in-flight refreshes.
    coordinator = RefreshClaimCoordinator(claimant_id="replica-a")
    owner_one = "fingerprint-old"
    owner_two = "fingerprint-reauth"

    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_one) is True
    # A different-fingerprint refresh in the same process must NOT re-enter the
    # live claim; it contends and loses until the first owner releases/expires.
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is False

    # Releasing the second owner is a no-op: it must not delete owner_one's claim.
    await coordinator.release(account_id, owner=owner_two)
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is False

    # Only the holding owner's release frees the claim.
    await coordinator.release(account_id, owner=owner_one)
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is True
    await coordinator.release(account_id, owner=owner_two)
