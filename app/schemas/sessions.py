"""``SessionCreate``, ``SessionPatch``, ``SessionView``, ``RosterView`` and
the management ``CancelRequest``.

Design: TECHNICAL_DESIGN.md Section 8.6.
"""

import uuid
from datetime import datetime
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.schemas.common import StrictModel

# Fields frozen once any participation exists (FR-02.10).
SCHEDULE_FIELDS = (
    "time_zone",
    "registration_opens_at",
    "registration_closes_at",
    "starts_at",
    "ends_at",
)
TIME_FIELDS = (
    "registration_opens_at",
    "registration_closes_at",
    "starts_at",
    "ends_at",
)
MAX_CAPACITY = 100_000


def _check_zone(v: str | None) -> str | None:
    """The database only checks nonblank; the IANA name is validated here."""
    if v is None:
        return v
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown IANA time zone") from exc
    return v


def times_in_order(
    opens: datetime, closes: datetime, starts: datetime, ends: datetime
) -> bool:
    """``opens < closes <= starts < ends``, the same rule as the ``time_order`` check."""
    return opens < closes <= starts < ends


class CancelRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=500)


class SessionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    time_zone: str
    registration_opens_at: AwareDatetime
    registration_closes_at: AwareDatetime
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    capacity: int = Field(gt=0, le=MAX_CAPACITY)

    @field_validator("time_zone")
    @classmethod
    def zone(cls, v: str) -> str:
        return _check_zone(v)

    @model_validator(mode="after")
    def time_order(self) -> Self:
        if not times_in_order(
            self.registration_opens_at,
            self.registration_closes_at,
            self.starts_at,
            self.ends_at,
        ):
            raise ValueError(
                "require registration_opens_at < registration_closes_at <= starts_at < ends_at"
            )
        return self


class SessionPatch(StrictModel):
    """All optional. Time order is checked in the service after merging with
    the stored values, because one field alone cannot be ordered."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    time_zone: str | None = None
    registration_opens_at: AwareDatetime | None = None
    registration_closes_at: AwareDatetime | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    capacity: int | None = Field(default=None, gt=0, le=MAX_CAPACITY)

    model_config = StrictModel.model_config | {
        "json_schema_extra": {"minProperties": 1}
    }

    @field_validator("time_zone")
    @classmethod
    def zone(cls, v: str | None) -> str | None:
        return _check_zone(v)

    @model_validator(mode="after")
    def not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class SessionView(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    status: str
    time_zone: str
    registration_opens_at: datetime
    registration_closes_at: datetime
    starts_at: datetime
    ends_at: datetime
    capacity: int
    occupied_count: int  # HELD + OFFERED + CONFIRMED
    available_count: int
    waitlist_count: int
    registration_status: str
    cancelled_at: datetime | None
    cancellation_reason: str | None
    version: int


class RosterEntry(BaseModel):
    participation_id: uuid.UUID
    account_id: uuid.UUID
    display_name: str
    email: str
    state: str
    deadline_at: datetime | None = None
    confirmed_at: datetime | None = None
    position: int | None = None  # WAITLISTED rows only, one-based


class RosterCounts(BaseModel):
    held: int
    offered: int
    confirmed: int
    waitlisted: int


class RosterView(BaseModel):
    session_id: uuid.UUID
    version: int
    counts: RosterCounts
    held: list[RosterEntry]
    offered: list[RosterEntry]
    confirmed: list[RosterEntry]
    waitlisted: list[RosterEntry]
