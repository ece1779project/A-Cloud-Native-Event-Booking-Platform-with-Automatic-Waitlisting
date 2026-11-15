import uuid
from datetime import datetime
from enum import StrEnum

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PublicationStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


_STATUS_CHECK = "status IN ('DRAFT', 'PUBLISHED', 'CANCELLED')"
# Cancellation fields are set together, exactly when the row is cancelled.
_CANCELLED_CHECK = (
    "(status = 'CANCELLED') = (cancelled_at IS NOT NULL) "
    "AND (cancelled_at IS NULL) = (cancellation_reason IS NULL) "
    "AND (cancellation_reason IS NULL OR length(btrim(cancellation_reason)) > 0)"
)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status_valid"),
        CheckConstraint(_CANCELLED_CHECK, name="cancellation_fields"),
        CheckConstraint("length(btrim(title)) > 0", name="title_nonblank"),
        CheckConstraint("length(btrim(description)) > 0", name="description_nonblank"),
        CheckConstraint("length(btrim(location)) > 0", name="location_nonblank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    organizer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text,
        default=PublicationStatus.DRAFT.value,
        server_default=PublicationStatus.DRAFT.value,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sessions: Mapped[list[EventSession]] = relationship(back_populates="event")


class EventSession(Base):
    __tablename__ = "event_sessions"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="status_valid"),
        CheckConstraint(_CANCELLED_CHECK, name="cancellation_fields"),
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
