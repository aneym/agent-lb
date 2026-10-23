from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResetCreditRedeemRequest(Base):
    """Durable (account, redeem_request_id) -> credit_id idempotency ledger.

    Written inside the per-account serialized redeem section BEFORE the
    upstream consume call so a retry carrying the same redeem_request_id —
    served by ANY replica — resolves to the originally selected credit and
    never burns a second one. Rows are purged opportunistically after 24h.
    """

    __tablename__ = "reset_credit_redeem_requests"

    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    redeem_request_id: Mapped[str] = mapped_column(String, primary_key=True)
    credit_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ResetCreditRedeemClaim(Base):
    """Cross-process per-account redeem serialization claim for SQLite.

    A single atomic conditional upsert (INSERT ... ON CONFLICT DO UPDATE ...
    WHERE expires_at < now) under SQLite's single-writer lock gives real
    multi-process mutual exclusion; the lease expiry recovers crashed holders.
    PostgreSQL deployments keep using pg_advisory_xact_lock instead.
    """

    __tablename__ = "reset_credit_redeem_claims"

    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    holder_id: Mapped[str] = mapped_column(String(100), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthFlowState(Base):
    """Durable dashboard OAuth add-account / reauth flow state.

    The dashboard OAuth flow (PKCE `code_verifier`, `state` token, device-code
    poll metadata, and status) is persisted here keyed by `flow_id` so that any
    replica behind a load balancer can complete a flow it did not start: the
    browser callback, a manually pasted callback URL, or a device-code status
    poll can land on a different replica than the one that ran `start`. The
    `code_verifier` is stored encrypted with the same key material as account
    tokens. Abandoned pending flows expire via `expires_at` and are purged
    opportunistically on write.
    """

    __tablename__ = "oauth_flow_states"

    flow_id: Mapped[str] = mapped_column(String, primary_key=True)
    state_token: Mapped[str | None] = mapped_column(String, nullable=True, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, default="openai", server_default="openai")
    method: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    intended_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    code_verifier_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    device_auth_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_code: Mapped[str | None] = mapped_column(String, nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthDeviceFlowSlot(Base):
    """Single-active-device-flow coordination slot (cross-replica).

    At most one dashboard device-code OAuth flow is "current" at a time. A
    device ``start`` atomically REPLACES the slot via a single conditional
    UPSERT on the fixed ``slot_key``, so two replicas starting device OAuth
    simultaneously leave exactly one current ``flow_id`` instead of two orphaned
    pending rows that both believe they are current. A poller atomically
    CONSUMES the slot (delete-if-mine) as its point of no return immediately
    before persisting tokens: a poller whose flow was superseded (the slot now
    names a different ``flow_id``) loses the consume and MUST abort without
    adding or re-authenticating an account. ``generation`` is a monotonic claim
    token bumped on every replacement, retained for observability.
    """

    __tablename__ = "oauth_device_flow_slots"

    slot_key: Mapped[str] = mapped_column(String, primary_key=True)
    flow_id: Mapped[str] = mapped_column(String, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeSentinel(Base):
    """Cross-replica consistency sentinels stamped into the shared database.

    Each row records a value every replica must agree on (for example the
    encryption-key fingerprint). Rows are written with an atomic
    insert-if-absent so the first replica to boot wins and later replicas
    verify against the stored value.
    """

    __tablename__ = "runtime_sentinels"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ModelRegistrySnapshotRecord(Base):
    """Single-row (id=1) persisted serialization of the refreshed model registry.

    Written by the leader's model refresh cycle and loaded by every replica via
    the cache-invalidation bus so the catalog stays replica-coherent.
    """

    __tablename__ = "model_registry_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    leader_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AccountRefreshClaim(Base):
    """Cross-replica, per-account token-refresh claim.

    One row per account marks which claimant (replica/process) currently owns
    the right to run the upstream OAuth token exchange. Rows are acquired via a
    conditional upsert that only succeeds when no unexpired claim by another
    claimant exists, and carry a TTL (`claim_expires_at`) so a crashed claimant
    can never block refresh indefinitely. The claim is pure coordination state:
    it holds no token material and is deleted after the refreshed tokens are
    persisted.
    """

    __tablename__ = "account_refresh_claims"

    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    claimed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    claim_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AccountPlanDowngradeObservation(Base):
    """Pending workspace-less paid -> free plan-downgrade evidence per account.

    A workspace-less usage payload reporting ``free`` for a paid account is only
    trusted once two consecutive refreshes agree (issue #1456). The observation
    count lives here rather than in process memory so the sequence is coherent
    across replicas sharing one database: an intervening paid payload observed by
    any replica clears the evidence for all of them, and two ``free`` samples
    split across replicas still converge.

    ``credential_fingerprint`` pins the evidence to the credential lineage that
    produced it: a fixed-salt digest over the account's stable seat identity
    (workspace and principal identifiers, email, ``codex_installation_id``),
    deliberately not over token material -- refresh tokens rotate on every
    token refresh, and rotation must not read as a credential replacement.
    Account ids are deterministic, so a delete-and-re-import or an in-place
    reauthentication reuses the same id with new credentials; those replacements
    discard this row explicitly (the accounts repository deletes it in the same
    transaction that applies the fresh credentials), and the fingerprint
    comparison restarts the count for any remaining path that rebinds the row's
    seat identity.

    The row holds no secrets: a count, a plan value, timestamps, and the
    non-reversible identity digest, stored outside the encrypted token columns.
    It is deleted as soon as the downgrade is applied or the evidence is
    invalidated. ``ondelete="CASCADE"`` drops it with the account.
    """

    __tablename__ = "account_plan_downgrade_observations"

    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    observations: Mapped[int] = mapped_column(Integer, nullable=False)
    credential_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_plan_type: Mapped[str] = mapped_column(String, nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
