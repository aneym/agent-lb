from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VerifierReceipt(Base):
    __tablename__ = "verifier_receipts"
    __table_args__ = (Index("ix_verifier_receipts_key", "repo", "head_sha", "contract_hash", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(String(128), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)
    seat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
