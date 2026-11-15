import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db import Base


class ParticipationState(StrEnum):
    HELD = "HELD"
    WAITLISTED = "WAITLISTED"
    OFFERED = "OFFERED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


ACTIVE_STATES = (
    ParticipationState.HELD,
    ParticipationState.WAITLISTED,
    ParticipationState.OFFERED,
    ParticipationState.CONFIRMED,
)
# States that occupy a seat (DATABASE_DESIGN Section 3).
OCCUPYING_STATES = (
    ParticipationState.HELD,
    ParticipationState.OFFERED,
    ParticipationState.CONFIRMED,
)


class TransitionReason(StrEnum):
    BOOKED = "BOOKED"
    ENQUEUED = "ENQUEUED"
    PROMOTED = "PROMOTED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"


class EndReason(StrEnum):
    USER_CANCELLED = "USER_CANCELLED"
    WITHDRAWN = "WITHDRAWN"
    OFFER_DECLINED = "OFFER_DECLINED"
    HOLD_EXPIRED = "HOLD_EXPIRED"
    OFFER_EXPIRED = "OFFER_EXPIRED"
    REGISTRATION_CLOSED = "REGISTRATION_CLOSED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    INELIGIBLE = "INELIGIBLE"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    EVENT_CANCELLED = "EVENT_CANCELLED"
    ADMIN_CANCELLED = "ADMIN_CANCELLED"


def _sql_list(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


_ALL_STATES = _sql_list(ParticipationState)
_ACTIVE = _sql_list(ACTIVE_STATES)
_END_REASONS = _sql_list(EndReason)
_CHANGE_REASONS = _sql_list([*TransitionReason, *EndReason])


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        CheckConstraint(f"state IN {_ALL_STATES}", name="state_valid"),
        CheckConstraint("queue_seq IS NULL OR queue_seq > 0", name="queue_seq_positive"),
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
            f"end_reason IS NULL OR end_reason IN {_END_REASONS}", name="end_reason_valid"
        ),
        Index(
            "uq_participations_active_account",
            "session_id",
            "account_id",
            unique=True,
            postgresql_where=text(f"state IN {_ACTIVE}"),
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


class ParticipationChange(Base):
    __tablename__ = "participation_changes"
    __table_args__ = (
        CheckConstraint("change_no >= 1", name="change_no_positive"),
        CheckConstraint(
            "(change_no = 1) = (from_state IS NULL)", name="first_has_no_source"
        ),
        CheckConstraint(
            f"from_state IS NULL OR from_state IN {_ALL_STATES}", name="from_state_valid"
        ),
        CheckConstraint(f"to_state IN {_ALL_STATES}", name="to_state_valid"),
        CheckConstraint(f"reason_code IN {_CHANGE_REASONS}", name="reason_code_valid"),
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


class RequestResult(Base):
    """Idempotency receipt for one authenticated command (DATABASE_DESIGN 2.4)."""

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
