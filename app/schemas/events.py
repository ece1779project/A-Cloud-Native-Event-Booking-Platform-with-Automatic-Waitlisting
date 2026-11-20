"""``EventCreate``, ``EventPatch`` and the event views.

Design: TECHNICAL_DESIGN.md Section 8.6.
"""

import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.models.event import Event
from app.schemas.common import StrictModel
from app.schemas.sessions import SessionView


class EventCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    location: str = Field(min_length=1, max_length=300)


class EventPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    location: str | None = Field(default=None, min_length=1, max_length=300)

    model_config = StrictModel.model_config | {"json_schema_extra": {"minProperties": 1}}

    @model_validator(mode="after")
    def not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class EventView(BaseModel):
    id: uuid.UUID
    organizer_id: uuid.UUID
    title: str
    description: str
    location: str
    status: str
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime
    next_session_starts_at: datetime | None = None  # list view only
    sessions: list[SessionView] | None = None  # GET /events/{event_id} only

    @classmethod
    def of(
        cls,
        event: Event,
        *,
        sessions: list[SessionView] | None = None,
        next_start: datetime | None = None,
    ) -> EventView:
        return cls(
            id=event.id,
            organizer_id=event.organizer_id,
            title=event.title,
            description=event.description,
            location=event.location,
            status=event.status,
            cancelled_at=event.cancelled_at,
            cancellation_reason=event.cancellation_reason,
            created_at=event.created_at,
            next_session_starts_at=next_start,
            sessions=sessions,
        )
