"""Table ``event_sessions``.

Design: DATABASE_DESIGN.md Section 2.2.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import PublicationStatus
from app.models.event import CANCELLED_CHECK, STATUS_CHECK

if TYPE_CHECKING:
    from app.models.event import Event


class EventSession(Base):
    __tablename__ = "event_sessions"
    __table_args__ = (
        CheckConstraint(STATUS_CHECK, name="status_valid"),
        CheckConstraint(CANCELLED_CHECK, name="cancellation_fields"),
        CheckConstraint("length(btrim(name)) > 0", name="name_nonblank"),
        CheckConstraint("length(btrim(time_zone)) > 0", name="time_zone_nonblank"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        CheckConstraint("last_queue_seq >= 0", name="last_queue_seq_nonnegative"),
        CheckConstraint("version >= 0", name="version_nonnegative"),
        CheckConstraint(
            "registration_opens_at < registration_closes_at "
            "AND registration_closes_at <= starts_at "
            "AND starts_at < ends_at",
            name="time_order",
        ),
        Index("ix_event_sessions_event_id_starts_at", "event_id", "starts_at"),
        Index(
            "ix_event_sessions_published_closes_at",
            "registration_closes_at",
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(Text)
    time_zone: Mapped[str] = mapped_column(Text)  # IANA zone, validated in the app
    status: Mapped[str] = mapped_column(
        Text,
        default=PublicationStatus.DRAFT.value,
        server_default=PublicationStatus.DRAFT.value,
    )
    registration_opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    registration_closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int]
    last_queue_seq: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    event: Mapped[Event] = relationship(back_populates="sessions")
