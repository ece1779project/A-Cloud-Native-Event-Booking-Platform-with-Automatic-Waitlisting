"""The domain state machine must match DATABASE_DESIGN Section 3 (and the
trg_participations_guard_update trigger)."""

from app.domain.enums import ParticipationState as S
from app.domain.transitions import (
    ACTIVE_STATES,
    ALLOWED_TRANSITIONS,
    OCCUPYING_STATES,
    TERMINAL_STATES,
    can_transition,
)


def test_state_partitions():
    assert set(ACTIVE_STATES) | set(TERMINAL_STATES) == set(S)
    assert set(OCCUPYING_STATES) == set(ACTIVE_STATES) - {S.WAITLISTED}


def test_terminal_states_have_no_outgoing_transitions():
    assert not any(src in TERMINAL_STATES for src, _ in ALLOWED_TRANSITIONS)


def test_examples():
    assert can_transition(S.WAITLISTED, S.OFFERED)
    assert can_transition(S.CONFIRMED, S.CANCELLED)
    assert not can_transition(S.WAITLISTED, S.CONFIRMED)
    assert not can_transition(S.HELD, S.OFFERED)
    assert not can_transition(S.EXPIRED, S.HELD)
