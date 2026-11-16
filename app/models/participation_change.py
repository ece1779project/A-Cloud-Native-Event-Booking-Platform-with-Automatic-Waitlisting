"""Table ``participation_changes``.

Append-only: the ``trg_participation_changes_append_only`` trigger rejects
UPDATE and DELETE.

Design: DATABASE_DESIGN.md Section 2.4.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import EndReason, TransitionReason
from app.models.participation import ALL_STATES_SQL, sql_list

_CHANGE_REASONS_SQL = sql_list([*TransitionReason, *EndReason])


class ParticipationChange(Base):
    __tablename__ = "participation_changes"
    __table_args__ = (
        CheckConstraint("change_no >= 1", name="change_no_positive"),
        CheckConstraint(
            "(change_no = 1) = (from_state IS NULL)", name="first_has_no_source"
        ),
        CheckConstraint(
            f"from_state IS NULL OR from_state IN {ALL_STATES_SQL}",
            name="from_state_valid",
        ),
        CheckConstraint(f"to_state IN {ALL_STATES_SQL}", name="to_state_valid"),
        CheckConstraint(
            f"reason_code IN {_CHANGE_REASONS_SQL}", name="reason_code_valid"
        ),
    )

    participation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("participations.id", ondelete="RESTRICT"), primary_key=True
    )
    change_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason_code: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    correlation_id: Mapped[uuid.UUID]
