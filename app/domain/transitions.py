"""The participation state machine: which states are active, which occupy a
seat, and which transition each operation may perform.

The database enforces the same transition table with the
``trg_participations_guard_update`` trigger. A change here needs a new
migration that replaces the trigger function;
``tests/integration/test_constraints.py`` fails when the two differ.

Design: DATABASE_DESIGN.md Section 3.
"""

from app.domain.enums import ParticipationState as S

ACTIVE_STATES: tuple[S, ...] = (S.HELD, S.WAITLISTED, S.OFFERED, S.CONFIRMED)
OCCUPYING_STATES: tuple[S, ...] = (S.HELD, S.OFFERED, S.CONFIRMED)
TERMINAL_STATES: tuple[S, ...] = (S.CANCELLED, S.EXPIRED)

ALLOWED_TRANSITIONS: frozenset[tuple[S, S]] = frozenset(
    {
        (S.WAITLISTED, S.OFFERED),
        (S.WAITLISTED, S.CANCELLED),
        (S.HELD, S.CONFIRMED),
        (S.HELD, S.EXPIRED),
        (S.HELD, S.CANCELLED),
        (S.OFFERED, S.CONFIRMED),
        (S.OFFERED, S.EXPIRED),
        (S.OFFERED, S.CANCELLED),
        (S.CONFIRMED, S.CANCELLED),
    }
)


def can_transition(source: S, target: S) -> bool:
    return (source, target) in ALLOWED_TRANSITIONS
