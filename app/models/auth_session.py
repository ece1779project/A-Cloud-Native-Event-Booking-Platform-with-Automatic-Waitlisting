"""Table ``auth_sessions``.

One row is one issued refresh token. A login starts a family; each refresh
retires the presented row and inserts its successor with the same
``family_id``, ``csrf_token_hash`` and ``expires_at``. A row is usable when
``rotated_at`` and ``revoked_at`` are null and ``expires_at`` is in the future.

Design: DATABASE_DESIGN.md Section 2.5.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoke_reason IS NULL)", name="revoked_pair"
        ),
        CheckConstraint(
            "revoke_reason IS NULL OR revoke_reason IN "
            "('LOGOUT', 'REUSE_DETECTED', 'ACCOUNT_DISABLED')",
            name="revoke_reason_valid",
        ),
        Index("ix_auth_sessions_account_id_expires_at", "account_id", "expires_at"),
        Index("ix_auth_sessions_family_id", "family_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    family_id: Mapped[uuid.UUID]
    refresh_token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    csrf_token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    user_agent_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_ip_prefix: Mapped[str | None] = mapped_column(Text)
