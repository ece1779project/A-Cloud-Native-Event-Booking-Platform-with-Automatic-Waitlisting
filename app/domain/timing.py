"""Time rules as pure functions of a supplied database time: registration
window status, the effective deadline of a new hold or offer, and whether
an allocation is overdue.

Callers pass ``clock_timestamp()`` read after their locks; nothing here
reads a clock.

Design: TECHNICAL_DESIGN.md Sections 6.1 and 6.3.
"""

from datetime import datetime, timedelta
from enum import StrEnum


class RegistrationStatus(StrEnum):
    NOT_OPEN = "NOT_OPEN"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


def registration_status(
    opens_at: datetime, closes_at: datetime, now: datetime
) -> RegistrationStatus:
    """The window is half-open: ``[opens_at, closes_at)``."""
    if now < opens_at:
        return RegistrationStatus.NOT_OPEN
    if now < closes_at:
        return RegistrationStatus.OPEN
    return RegistrationStatus.CLOSED


def allocation_deadline(
    now: datetime, validity_seconds: int, closes_at: datetime
) -> datetime:
    """``min(allocation time + validity, registration close)`` (FR-05.2)."""
    return min(now + timedelta(seconds=validity_seconds), closes_at)


def is_overdue(deadline_at: datetime, now: datetime) -> bool:
    """A deadline is reached at the instant itself (FR-05.6)."""
    return now >= deadline_at


def has_started(starts_at: datetime, now: datetime) -> bool:
    return now >= starts_at
