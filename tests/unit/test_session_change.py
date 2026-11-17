"""The internal SessionChange result and its enums (TECHNICAL_DESIGN 10.1, 10.3)."""

import uuid

import pytest
from pydantic import ValidationError

from app.domain.enums import ParticipationState
from app.schemas.realtime import (
    REASON_BY_STATE,
    ChangeCause,
    ParticipationChangedReason,
    ParticipationTransition,
    SessionChange,
)


def test_enum_values_match_the_design():
    assert {c.value for c in ChangeCause} == {
        "booking_hold",
        "waitlist_join",
        "confirmation",
        "attendee_release",
        "admin_participation_cancel",
        "expiration",
        "promotion",
        "registration_closed",
        "capacity_changed",
        "session_published",
        "event_published",
        "session_cancelled",
        "event_cancelled",
    }
    assert {r.value for r in ParticipationChangedReason} == {
        "hold_created",
        "waitlist_joined",
        "offer_created",
        "confirmed",
        "cancelled",
        "expired",
    }


def test_every_state_maps_to_its_own_reason():
    assert set(REASON_BY_STATE) == set(ParticipationState)
    assert set(REASON_BY_STATE.values()) == set(ParticipationChangedReason)


def test_session_change_is_frozen_and_defaults_to_no_transitions():
    transition = ParticipationTransition(
        participation_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        reason=REASON_BY_STATE[ParticipationState.OFFERED],
    )
    change = SessionChange(
        event_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        version=3,
        cause=ChangeCause.EXPIRATION,
        transitions=(transition,),
    )
    assert change.transitions[0].reason == "offer_created"
    with pytest.raises(ValidationError):
        change.version = 4

    empty = SessionChange(
        event_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        version=1,
        cause=ChangeCause.CAPACITY_CHANGED,
    )
    assert empty.transitions == ()
