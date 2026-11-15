import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(StrEnum):
    ATTENDEE = "ATTENDEE"
    ORGANIZER = "ORGANIZER"
    ADMIN = "ADMIN"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("role IN ('ATTENDEE', 'ORGANIZER', 'ADMIN')", name="role_valid"),
        CheckConstraint("length(btrim(email)) > 0", name="email_nonblank"),
        CheckConstraint("length(btrim(display_name)) > 0", name="display_name_nonblank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(
        Text, default=Role.ATTENDEE.value, server_default=Role.ATTENDEE.value
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# Case-insensitive email uniqueness (emails are trimmed on input).
Index("uq_accounts_email_lower", func.lower(Account.email), unique=True)
