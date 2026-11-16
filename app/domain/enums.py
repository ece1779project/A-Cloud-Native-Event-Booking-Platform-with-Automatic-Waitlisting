"""Enumerations stored in the database and exposed by the API: account role,
event and session status, participation state, history reason codes,
terminal reason codes, refresh-token revoke reasons and command result
codes.

Design: DATABASE_DESIGN.md Sections 2 and 3; TECHNICAL_DESIGN.md Section 8.5.
"""

from enum import StrEnum


class Role(StrEnum):
    ATTENDEE = "ATTENDEE"
    ORGANIZER = "ORGANIZER"
    ADMIN = "ADMIN"


class PublicationStatus(StrEnum):
    """Status of both events and event sessions."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class ParticipationState(StrEnum):
    HELD = "HELD"
    WAITLISTED = "WAITLISTED"
    OFFERED = "OFFERED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TransitionReason(StrEnum):
    """Reason codes for non-terminal transitions in participation history."""

    BOOKED = "BOOKED"
    ENQUEUED = "ENQUEUED"
    PROMOTED = "PROMOTED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"


class EndReason(StrEnum):
    """Terminal reason codes for CANCELLED and EXPIRED attempts."""

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


class RevokeReason(StrEnum):
    LOGOUT = "LOGOUT"
    REUSE_DETECTED = "REUSE_DETECTED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
