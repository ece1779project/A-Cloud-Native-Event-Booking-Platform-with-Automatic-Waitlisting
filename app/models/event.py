"""Table ``events``.

Design: DATABASE_DESIGN.md Section 2.1.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import PublicationStatus

if TYPE_CHECKING:
    from app.models.event_session import EventSession

STATUS_CHECK = "status IN ('DRAFT', 'PUBLISHED', 'CANCELLED')"
# Cancellation fields are set together, exactly when the row is cancelled.
CANCELLED_CHECK = (
    "(status = 'CANCELLED') = (cancelled_at IS NOT NULL) "
    "AND (cancelled_at IS NULL) = (cancellation_reason IS NULL) "
    "AND (cancellation_reason IS NULL OR length(btrim(cancellation_reason)) > 0)"
)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(STATUS_CHECK, name="status_valid"),
        CheckConstraint(CANCELLED_CHECK, name="cancellation_fields"),
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
