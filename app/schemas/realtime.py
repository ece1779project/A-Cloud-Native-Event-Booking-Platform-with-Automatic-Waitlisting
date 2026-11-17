"""One model per WebSocket message type and one enum per ``reason`` list,
plus the ticket request and response. Also the internal ``SessionChange``
result, which is not a WebSocket message. No other module spells out a
message body by hand.

Design: TECHNICAL_DESIGN.md Sections 10.1, 10.3, and 10.5.
"""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.domain.enums import ParticipationState

if TYPE_CHECKING:
    from app.models.participation import Participation

# Internal result: SessionChange (TECHNICAL_DESIGN 10.3)
# Returned by the service that owns a transaction and passed to the publisher
# after commit. Not a wire message, so it has no contract fixture.


class ChangeCause(StrEnum):
    """The operation that started a transaction. Never sent on the wire."""

    BOOKING_HOLD = "booking_hold"
    WAITLIST_JOIN = "waitlist_join"
    CONFIRMATION = "confirmation"
    ATTENDEE_RELEASE = "attendee_release"  # cancel, decline and leave
    ADMIN_PARTICIPATION_CANCEL = "admin_participation_cancel"
    EXPIRATION = "expiration"
    PROMOTION = "promotion"  # only the worker promotion scan
    REGISTRATION_CLOSED = "registration_closed"
    CAPACITY_CHANGED = "capacity_changed"
    SESSION_PUBLISHED = "session_published"
    EVENT_PUBLISHED = "event_published"
    SESSION_CANCELLED = "session_cancelled"
    EVENT_CANCELLED = "event_cancelled"


class ParticipationChangedReason(StrEnum):
    """``reason`` values of the ``participation.changed`` message (10.1)."""

    HOLD_CREATED = "hold_created"
    WAITLIST_JOINED = "waitlist_joined"
    OFFER_CREATED = "offer_created"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# The reason follows from the state a transition enters, one to one.
REASON_BY_STATE: dict[ParticipationState, ParticipationChangedReason] = {
    ParticipationState.HELD: ParticipationChangedReason.HOLD_CREATED,
    ParticipationState.WAITLISTED: ParticipationChangedReason.WAITLIST_JOINED,
    ParticipationState.OFFERED: ParticipationChangedReason.OFFER_CREATED,
    ParticipationState.CONFIRMED: ParticipationChangedReason.CONFIRMED,
    ParticipationState.CANCELLED: ParticipationChangedReason.CANCELLED,
    ParticipationState.EXPIRED: ParticipationChangedReason.EXPIRED,
}


class ParticipationTransition(BaseModel):
    """One participation state change written by a transaction."""

    model_config = ConfigDict(frozen=True)

    participation_id: uuid.UUID
    account_id: uuid.UUID
    reason: ParticipationChangedReason

    @classmethod
    def of(cls, participation: Participation) -> ParticipationTransition:
        """Describe a participation row right after its state changed."""
        return cls(
            participation_id=participation.id,
            account_id=participation.account_id,
            reason=REASON_BY_STATE[ParticipationState(participation.state)],
        )


class SessionChange(BaseModel):
    """One session version advanced by one commit."""

    model_config = ConfigDict(frozen=True)

    event_id: uuid.UUID
    session_id: uuid.UUID
    version: int
    cause: ChangeCause
    transitions: tuple[ParticipationTransition, ...] = ()


# WebSocket messages (TECHNICAL_DESIGN 10.1 and 10.5)
