"""Table ``participations``.

Design: DATABASE_DESIGN.md Section 2.3.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import EndReason, ParticipationState
from app.domain.transitions import ACTIVE_STATES


def sql_list(values) -> str:
    """Render enum values as a SQL ``IN`` list for check constraints."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


ALL_STATES_SQL = sql_list(ParticipationState)
_ACTIVE_SQL = sql_list(ACTIVE_STATES)
_END_REASONS_SQL = sql_list(EndReason)


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        CheckConstraint(f"state IN {ALL_STATES_SQL}", name="state_valid"),
        CheckConstraint(
            "queue_seq IS NULL OR queue_seq > 0", name="queue_seq_positive"
        ),
        CheckConstraint(
            "state NOT IN ('WAITLISTED', 'OFFERED') OR queue_seq IS NOT NULL",
            name="queued_has_seq",
        ),
        CheckConstraint("state <> 'HELD' OR queue_seq IS NULL", name="held_has_no_seq"),
        CheckConstraint(
            "(allocated_at IS NULL) = (deadline_at IS NULL) "
            "AND (deadline_at IS NULL OR deadline_at > allocated_at)",
            name="allocation_pair",
        ),
        CheckConstraint(
            "state <> 'WAITLISTED' OR allocated_at IS NULL", name="waiting_unallocated"
        ),
        CheckConstraint(
            "state NOT IN ('HELD', 'OFFERED', 'CONFIRMED', 'EXPIRED') "
            "OR allocated_at IS NOT NULL",
            name="allocated_states",
        ),
        CheckConstraint(
            "confirmed_at IS NULL OR state IN ('CONFIRMED', 'CANCELLED')",
            name="confirmed_at_states",
        ),
        CheckConstraint(
            "state <> 'CONFIRMED' OR (confirmed_at IS NOT NULL "
            "AND allocated_at <= confirmed_at AND confirmed_at < deadline_at)",
            name="confirmed_window",
        ),
        CheckConstraint(
            "(state IN ('CANCELLED', 'EXPIRED')) = (ended_at IS NOT NULL) "
            "AND (ended_at IS NULL) = (end_reason IS NULL)",
            name="ended_fields",
        ),
        CheckConstraint(
            f"end_reason IS NULL OR end_reason IN {_END_REASONS_SQL}",
            name="end_reason_valid",
        ),
        Index(
            "uq_participations_active_account",
            "session_id",
            "account_id",
            unique=True,
            postgresql_where=text(f"state IN {_ACTIVE_SQL}"),
        ),
        Index(
            "uq_participations_queue_seq",
            "session_id",
            "queue_seq",
            unique=True,
            postgresql_where=text("queue_seq IS NOT NULL"),
        ),
        Index("ix_participations_session_id_state", "session_id", "state"),
        Index(
            "ix_participations_waitlist_fifo",
            "session_id",
            "queue_seq",
            postgresql_where=text("state = 'WAITLISTED'"),
        ),
        Index(
            "ix_participations_account_id_created_at",
            "account_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_participations_due_deadline",
            "deadline_at",
            "session_id",
            postgresql_where=text("state IN ('HELD', 'OFFERED')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event_sessions.id", ondelete="RESTRICT")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(Text)
    queue_seq: Mapped[int | None] = mapped_column(BigInteger)
    # Writers set created_at to the clock_timestamp() sampled under the lock.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    allocated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)
