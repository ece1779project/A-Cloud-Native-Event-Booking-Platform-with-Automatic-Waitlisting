"""Table ``request_results``: one idempotency receipt per authenticated
command, keyed by account and client-supplied request ID.

Design: DATABASE_DESIGN.md Section 2.4.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RequestResult(Base):
    __tablename__ = "request_results"
    __table_args__ = (
        CheckConstraint("http_status BETWEEN 100 AND 599", name="http_status_range"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), primary_key=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    operation: Mapped[str] = mapped_column(Text)
    parameter_hash: Mapped[bytes] = mapped_column(LargeBinary)
    participation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("participations.id", ondelete="RESTRICT")
    )
    http_status: Mapped[int] = mapped_column(SmallInteger)
    result_code: Mapped[str] = mapped_column(Text)
    result_body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
