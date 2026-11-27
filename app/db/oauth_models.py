from __future__ import annotations

from sqlalchemy import Float, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OAuthFlow(Base):
    __tablename__ = "oauth_flows"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    state_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    secrets_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    error_message: Mapped[str | None] = mapped_column(Text)
    probe_status_code: Mapped[int | None]
    verification_deadline: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    interval_seconds: Mapped[int | None]
    poll_after: Mapped[float | None] = mapped_column(Float)
    poll_owner: Mapped[str | None] = mapped_column(String(64))
