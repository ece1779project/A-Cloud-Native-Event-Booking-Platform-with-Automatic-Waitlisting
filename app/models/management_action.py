"""Table ``management_actions``.

Append-only audit of administrative and organizer management actions; the
``trg_management_actions_append_only`` trigger rejects UPDATE and DELETE.

Design: DATABASE_DESIGN.md Section 2.4.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ManagementAction(Base):
    __tablename__ = "management_actions"
    __table_args__ = (
        CheckConstraint("length(btrim(action)) > 0", name="action_nonblank"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_nonblank"),
        CheckConstraint(
            "num_nonnulls(target_account_id, target_event_id, "
            "target_session_id, target_participation_id) = 1",
            name="exactly_one_target",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    correlation_id: Mapped[uuid.UUID]
    target_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    target_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT")
    )
    target_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("event_sessions.id", ondelete="RESTRICT")
    )
    target_participation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("participations.id", ondelete="RESTRICT")
    )
