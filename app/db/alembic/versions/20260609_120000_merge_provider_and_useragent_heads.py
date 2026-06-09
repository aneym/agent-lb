"""merge provider-dimension and weekly/monthly/useragent heads

Collapses the two divergent Alembic heads that accumulated on this branch:

- ``20260528_172900_add_provider_dimension`` (Anthropic provider lineage)
- ``20260607_000000_merge_weekly_monthly_useragent_heads`` (upstream main:
  useragent fields, upstream-proxy routing, weekly pace, monthly window, reauth)

Both lineages are additive and non-overlapping, so this is a pure no-op merge
that gives ``alembic upgrade head`` a single unambiguous target again. Required
so the service can run startup migrations cleanly on restart.

Revision ID: 20260609_120000_merge_provider_and_useragent_heads
Revises:
- 20260528_172900_add_provider_dimension
- 20260607_000000_merge_weekly_monthly_useragent_heads
Create Date: 2026-06-09 12:00:00.000000
"""

from __future__ import annotations

revision = "20260609_120000_merge_provider_and_useragent_heads"
down_revision = (
    "20260528_172900_add_provider_dimension",
    "20260607_000000_merge_weekly_monthly_useragent_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
