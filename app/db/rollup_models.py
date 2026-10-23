from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountUsageRollup(Base):
    """Folded lifetime request-usage sums per account.

    Request-log rows older than the fold watermark (the single
    ``account_usage_rollup_state`` row) are summed here; account usage
    summaries add a live aggregate over only the newer request-log tail.
    Sums are stored unclamped; the ``cached_input_tokens <= input_tokens``
    clamp applies after merging.
    """

    __tablename__ = "account_usage_rollups"

    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    cache_creation_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"), nullable=False)


class ApiKeyUsageRollup(Base):
    """Folded lifetime request-usage sums per API key.

    Governed by the same fold watermark as ``AccountUsageRollup`` (the single
    ``account_usage_rollup_state`` row); sums use the API-key summary
    semantics (no dedupe, soft-deleted rows included, warmup kinds excluded).
    Stored unclamped; the ``cached <= input`` clamp applies after merging
    with the live tail.
    """

    __tablename__ = "api_key_usage_rollups"

    api_key_id: Mapped[str] = mapped_column(String, ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True)
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"), nullable=False)


class AccountUsageRollupState(Base):
    """Single-row fold watermark (naive-UTC), seeded by the migration.

    Keeping the watermark on a dedicated always-present row gives fold passes
    something to ``SELECT ... FOR UPDATE`` even before any rollup rows exist,
    serializing concurrent backfills, and lets reads fetch sums + watermark in
    one statement (one snapshot).
    """

    __tablename__ = "account_usage_rollup_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    folded_through: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Hour-aligned watermark for the time-axis rollups below. Kept as a
    # SEPARATE column (not shared with `folded_through`) so the hourly
    # backfill never resets or rewrites the lifetime rollups, which cannot
    # be recomputed once retention has pruned raw request logs. Invariant:
    # always a whole UTC hour (epoch % 3600 == 0); rows with
    # `requested_at < hourly_folded_through` are fully folded, newer rows
    # are the live tail.
    hourly_folded_through: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("'1970-01-01 00:00:00'"),
    )
    # Watermark for the conversation presence satellite. Separate from
    # `hourly_folded_through` (same alignment invariant, same state row and
    # row lock) so the satellite added later backfills from epoch without
    # rewinding — and without being gated on — the other time-axis rollups.
    conversation_folded_through: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("'1970-01-01 00:00:00'"),
    )
    # Start of the hourly range a legacy (pre-cancelled_count) fold pass may
    # have written after the migration ran (#1552 rolling-upgrade fence).
    # The migration stamps existing rows with their hourly_folded_through;
    # the epoch server default covers a state row bootstrapped by an OLD
    # replica after the migration (its entire backfill is legacy-folded).
    # Only NEW code writes NULL, after refolding [marker, watermark) from
    # raw — so NULL always means "no legacy-suspect range outstanding".
    upgrade_repair_from: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        server_default=text("'1970-01-01 00:00:00'"),
    )


class RequestUsageHourlyRollup(Base):
    """Hour-bucketed request-usage sums (time-axis rollup).

    One row per UTC hour x dimension combination, folded from raw
    ``request_logs`` below the ``hourly_folded_through`` watermark. Rows are
    written by the hourly fold pass (DELETE-then-INSERT per slice, so
    re-folds always converge) and mutated afterwards only by the account
    lifecycle mirrors (soft/hard delete, consolidation) — never recomputed
    from raw, so buckets survive request-log retention pruning.

    Nullable raw dimensions (``account_id``/``api_key_id``/``service_tier``)
    are stored via the collision-free NULL-sentinel encoding
    (``usage_time_rollup.to_dimension``) so they can participate in the
    primary key on both dialects (UNIQUE treats NULLs as distinct) without
    conflating NULL with a legitimate empty string. ``request_kind`` is
    NOT NULL at the source and stored verbatim (warmup kinds included; reads
    filter by dimension). No FKs: rollup rows must outlive account deletion.
    """

    __tablename__ = "request_usage_hourly_rollups"

    bucket_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    api_key_id: Mapped[str] = mapped_column(String, primary_key=True)
    model: Mapped[str] = mapped_column(String, primary_key=True)
    service_tier: Mapped[str] = mapped_column(String, primary_key=True)
    request_kind: Mapped[str] = mapped_column(String, primary_key=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=False, server_default=false())
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    # sum(status NOT IN ('success', 'cancelled')) — status is folded as a
    # measure, not a dimension. Rows folded before cancelled_count existed
    # keep the legacy sum(status != 'success') fold (no backfill; a disclosed
    # step change on error-rate trends).
    error_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    # sum(status = 'cancelled') — client-side disconnect terminals, split out
    # of error_count so dashboards can show success/cancelled/error distinctly.
    # 0 (server default) on rows folded before the measure existed.
    cancelled_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    # sum(coalesce(output_tokens, reasoning_tokens, 0)) — not derivable from
    # the two sums above (planner / trends / usage-summary semantics).
    output_or_reasoning_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    # sum(max(0, min(coalesce(cached, 0), coalesce(input, 0)))) — pre-folded
    # for the future usage-summary switch (one-way door once raw is pruned).
    cached_input_tokens_clamped: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"), nullable=False)
    # count(cost_usd IS NOT NULL) — preserves the "all-NULL model excluded"
    # rule of cost-by-model aggregations.
    cost_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)


class RequestUsageHourlyErrorRollup(Base):
    """Hour-bucketed error-code counts (top-error satellite).

    ``error_code`` has unbounded cardinality, so it is isolated from the main
    hourly rollup. Fold filter reproduces the top-error read exactly:
    non-warmup kinds, ``status NOT IN ('success', 'cancelled')``,
    ``error_code IS NOT NULL`` (soft-deleted rows included). Rows folded
    before cancelled rows left the error fold still carry
    ``client_disconnected`` counts; the top-error reads exclude that code.
    ``account_id`` is carried only so account hard-deletion can mirror raw
    row removal.
    """

    __tablename__ = "request_usage_hourly_error_rollups"

    bucket_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    error_code: Mapped[str] = mapped_column(String, primary_key=True)
    error_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)


class RequestDemandQuarterRollup(Base):
    """Quarter-hour demand sums for the quota planner (the only sub-hour
    consumer, ``DEFAULT_SLOT_SECONDS = 900``).

    The FULL legacy demand grain (account, api_key, model, reasoning_effort,
    request_kind, status) is preserved as dimensions: the planner's
    ``_bin_demand_units`` applies ``max(token, cost, request units)`` per bin
    BEFORE summing (nonlinear), so a coarser fold would change forecasts
    wherever a slot mixes groups with different dominant components. The row
    count equals what the legacy runtime ``GROUP BY`` returned per query.

    ``is_deleted`` is a dimension (not a fold-time filter) and ``account_id``
    a carried key so account soft/hard deletion — which retroactively detaches
    or removes the account's entire raw history — can be mirrored here instead
    of permanently diverging from the planner's ``deleted_at IS NULL`` view.
    """

    __tablename__ = "request_demand_quarter_rollups"

    slot_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    api_key_id: Mapped[str] = mapped_column(String, primary_key=True)
    model: Mapped[str] = mapped_column(String, primary_key=True)
    reasoning_effort: Mapped[str] = mapped_column(String, primary_key=True)
    request_kind: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, primary_key=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=False, server_default=false())
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    output_or_reasoning_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"), nullable=False)


class RequestConversationHourlyRollup(Base):
    """Hour-bucketed conversation presence (distinct-conversation satellite).

    One row means "this conversation had ``request_count`` countable requests
    in this UTC hour under this (account, is_deleted) attribution". Distinct
    conversation counts are not additive across buckets, so the reads UNION
    the folded ``conversation_id`` values with the raw live tail and count
    distinct over the merge; ``request_count`` stays additive for the
    conversation-request totals.

    ``conversation_id`` is the normalized value (whitespace-trimmed,
    non-empty — NULL/blank rows are excluded by the fold filter, matching
    every reader). ``is_deleted`` is a dimension because the dashboard
    conversation reads exclude soft-deleted rows while the reports reads
    include them; ``account_id`` (NULL-sentinel encoded) is carried only so
    the account lifecycle mirrors can re-attribute or remove folded presence
    exactly as the raw mutation does. No FKs: rows must outlive accounts.
    """

    __tablename__ = "request_conversation_hourly_rollups"

    bucket_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=False, server_default=false())
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
