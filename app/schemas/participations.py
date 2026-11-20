"""``ConfirmRequest``, ``ParticipationView`` and the stored command result
returned by ``GET /requests/{request_id}``.

Design: TECHNICAL_DESIGN.md Section 8.6.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import StrictModel


class ConfirmRequest(StrictModel):
    """Simulated payment only: no card or bank fields are accepted (8.4)."""

    simulation_result: Literal["success", "failure"]


class EmptyBody(StrictModel):
    """``{}`` for book, waitlist, cancel, decline and leave; any field is a 422."""


class SessionSummary(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    event_title: str
    name: str
    time_zone: str
    starts_at: datetime
    ends_at: datetime
    registration_closes_at: datetime
    status: str
    event_status: str


class ParticipationView(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    account_id: uuid.UUID
    state: str
    queue_position: int | None  # one-based, only while WAITLISTED
    created_at: datetime
    allocated_at: datetime | None
    deadline_at: datetime | None  # absolute, so the client's countdown needs no server clock
    confirmed_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    display_label: str | None = None  # "Event cancelled" when the parent event is cancelled
    historical: bool = False
    session: SessionSummary
    version: int  # current session version
