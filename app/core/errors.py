"""Application exception hierarchy and the stable error codes with their HTTP
status and ``retryable`` flag.

Services raise these; ``app.api.exception_handlers`` renders them.

Design: TECHNICAL_DESIGN.md Section 8.5.
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    FORBIDDEN = "FORBIDDEN"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    REGISTRATION_NOT_OPEN = "REGISTRATION_NOT_OPEN"
    REGISTRATION_CLOSED = "REGISTRATION_CLOSED"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    EVENT_CANCELLED = "EVENT_CANCELLED"
    PARTICIPATION_EXPIRED = "PARTICIPATION_EXPIRED"
    SESSION_STARTED = "SESSION_STARTED"
    CAPACITY_BELOW_OCCUPIED = "CAPACITY_BELOW_OCCUPIED"
    SCHEDULE_FROZEN = "SCHEDULE_FROZEN"
    EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED"
    WAITLIST_REQUIRED = "WAITLIST_REQUIRED"
    DIRECT_BOOKING_AVAILABLE = "DIRECT_BOOKING_AVAILABLE"
    REFRESH_ALREADY_ROTATED = "REFRESH_ALREADY_ROTATED"
    RATE_LIMITED = "RATE_LIMITED"
    LOCK_TIMEOUT = "LOCK_TIMEOUT"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_SPEC: dict[ErrorCode, tuple[int, bool, str]] = {
    ErrorCode.VALIDATION_ERROR: (422, False, "The request is invalid."),
    ErrorCode.AUTHENTICATION_REQUIRED: (401, False, "Authentication is required."),
    ErrorCode.INVALID_CREDENTIALS: (401, False, "Email or password is incorrect."),
    ErrorCode.TOKEN_EXPIRED: (401, False, "The access token has expired."),
    ErrorCode.FORBIDDEN: (403, False, "You are not allowed to perform this action."),
    ErrorCode.ACCOUNT_DISABLED: (403, False, "This account is disabled."),
    ErrorCode.RESOURCE_NOT_FOUND: (404, False, "The resource does not exist."),
    ErrorCode.IDEMPOTENCY_CONFLICT: (
        409,
        False,
        "This Idempotency-Key was used with different inputs.",
    ),
    ErrorCode.INVALID_STATE_TRANSITION: (
        409,
        False,
        "The operation is not allowed in the current state.",
    ),
    ErrorCode.REGISTRATION_NOT_OPEN: (409, False, "Registration has not opened yet."),
    ErrorCode.REGISTRATION_CLOSED: (409, False, "Registration has closed."),
    ErrorCode.SESSION_CANCELLED: (409, False, "The session is cancelled."),
    ErrorCode.EVENT_CANCELLED: (409, False, "The event is cancelled."),
    ErrorCode.PARTICIPATION_EXPIRED: (409, False, "The hold or offer has expired."),
    ErrorCode.SESSION_STARTED: (409, False, "The session has already started."),
    ErrorCode.CAPACITY_BELOW_OCCUPIED: (
        409,
        False,
        "Capacity cannot go below occupied seats.",
    ),
    ErrorCode.SCHEDULE_FROZEN: (
        409,
        False,
        "Session times cannot change once registrations exist.",
    ),
    ErrorCode.EMAIL_ALREADY_REGISTERED: (
        409,
        False,
        "This email is already registered.",
    ),
    ErrorCode.WAITLIST_REQUIRED: (
        409,
        False,
        "No seat can be booked directly while the session is full or queued.",
    ),
    ErrorCode.DIRECT_BOOKING_AVAILABLE: (
        409,
        False,
        "A seat is available; book it directly.",
    ),
    ErrorCode.REFRESH_ALREADY_ROTATED: (
        409,
        False,
        "This refresh token was just rotated.",
    ),
    ErrorCode.RATE_LIMITED: (429, True, "Too many requests."),
    ErrorCode.LOCK_TIMEOUT: (503, True, "The session is busy; retry shortly."),
    ErrorCode.DATABASE_UNAVAILABLE: (503, True, "The database is unavailable."),
    ErrorCode.SERVICE_UNAVAILABLE: (503, True, "The service is unavailable."),
    ErrorCode.INTERNAL_ERROR: (500, False, "Unexpected server error."),
}


def status_of(code: ErrorCode) -> int:
    return _SPEC[code][0]


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        session_version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        status, retryable, default_message = _SPEC[code]
        super().__init__(message or default_message)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.message = message or default_message
        self.details = details or {}
        self.session_version = session_version
        self.headers = headers or {}
