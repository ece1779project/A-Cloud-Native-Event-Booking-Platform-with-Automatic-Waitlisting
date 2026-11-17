"""Minimal row builders for database tests. They bypass business rules on purpose.

Each participation builder also writes the history a real service would have
written, so later transitions number correctly. Times come from the test
process clock, which matches the database clock closely enough for
minute-scale windows and deadlines.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.participation_store import record_change
from app.domain.enums import (
    ParticipationState,
    PublicationStatus,
    Role,
    TransitionReason,
)
from app.models.account import Account
from app.models.event import Event
from app.models.event_session import EventSession
from app.models.participation import Participation

Window = Literal["open", "closed", "not_open"]

# Registration window relative to now, in hours: opens, closes, starts, ends.
_WINDOWS: dict[Window, tuple[int, int, int, int]] = {
    "open": (-1, 1, 2, 3),
    "closed": (-2, -1, 1, 2),
    "not_open": (1, 2, 3, 4),
}


async def make_account(
    db: AsyncSession,
    *,
    role: Role = Role.ATTENDEE,
    email: str | None = None,
    disabled: bool = False,
) -> Account:
    account = Account(
        email=email or f"{uuid.uuid4().hex[:12]}@example.com",
        display_name="Test User",
        password_hash="not-a-real-hash",
        role=role.value,
        disabled_at=datetime.now(UTC) if disabled else None,
    )
    db.add(account)
    await db.flush()
    return account


async def make_event(db: AsyncSession, organizer: Account) -> Event:
    event = Event(
        organizer_id=organizer.id,
        title="Workshop",
        description="Hands-on session",
        location="Toronto",
        status=PublicationStatus.PUBLISHED.value,
    )
    db.add(event)
    await db.flush()
    return event


async def make_session(
    db: AsyncSession,
    event: Event,
    *,
    capacity: int = 2,
    now: datetime | None = None,
    status: PublicationStatus = PublicationStatus.PUBLISHED,
    window: Window = "open",
) -> EventSession:
    now = now or datetime.now(UTC)
    opens, closes, starts, ends = (now + timedelta(hours=h) for h in _WINDOWS[window])
    session = EventSession(
        event_id=event.id,
        name="Morning",
        time_zone="America/Toronto",
        status=status.value,
        registration_opens_at=opens,
        registration_closes_at=closes,
        starts_at=starts,
        ends_at=ends,
        capacity=capacity,
    )
    db.add(session)
    await db.flush()
    return session


def _allocation(minutes: int, overdue: bool) -> tuple[datetime, datetime]:
    """(allocated_at, deadline_at). An overdue allocation was made ten minutes
    ago and its deadline passed one minute ago."""
    now = datetime.now(UTC)
    if overdue:
        return now - timedelta(minutes=10), now - timedelta(minutes=1)
    return now, now + timedelta(minutes=minutes)


S = ParticipationState
R = TransitionReason


async def _add(
    db: AsyncSession,
    participation: Participation,
    history: list[
        tuple[ParticipationState | None, ParticipationState, TransitionReason]
    ],
) -> Participation:
    """Insert the row and its history: (from_state, to_state, reason) steps."""
    db.add(participation)
    await db.flush()
    occurred_at = participation.allocated_at or datetime.now(UTC)
    correlation_id = uuid.uuid4()
    for from_state, to_state, reason in history:
        await record_change(
            db,
            participation_id=participation.id,
            from_state=from_state,
            to_state=to_state,
            reason_code=reason,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            # Promotion is a system transition; the others are the attendee's.
            actor_id=None if reason == R.PROMOTED else participation.account_id,
        )
    return participation


async def make_held(
    db: AsyncSession,
    session: EventSession,
    account: Account,
    *,
    minutes: int = 5,
    overdue: bool = False,
) -> Participation:
    allocated_at, deadline_at = _allocation(minutes, overdue)
    return await _add(
        db,
        Participation(
            session_id=session.id,
            account_id=account.id,
            state=S.HELD.value,
            created_at=allocated_at,
            allocated_at=allocated_at,
            deadline_at=deadline_at,
        ),
        [(None, S.HELD, R.BOOKED)],
    )


async def make_waiting(
    db: AsyncSession, session: EventSession, account: Account, queue_seq: int
) -> Participation:
    """Does not advance ``session.last_queue_seq``."""
    return await _add(
        db,
        Participation(
            session_id=session.id,
            account_id=account.id,
            state=S.WAITLISTED.value,
            queue_seq=queue_seq,
        ),
        [(None, S.WAITLISTED, R.ENQUEUED)],
    )


async def make_offered(
    db: AsyncSession,
    session: EventSession,
    account: Account,
    queue_seq: int,
    *,
    minutes: int = 10,
    overdue: bool = False,
) -> Participation:
    allocated_at, deadline_at = _allocation(minutes, overdue)
    return await _add(
        db,
        Participation(
            session_id=session.id,
            account_id=account.id,
            state=S.OFFERED.value,
            queue_seq=queue_seq,
            allocated_at=allocated_at,
            deadline_at=deadline_at,
        ),
        [(None, S.WAITLISTED, R.ENQUEUED), (S.WAITLISTED, S.OFFERED, R.PROMOTED)],
    )


async def make_confirmed(
    db: AsyncSession, session: EventSession, account: Account
) -> Participation:
    allocated_at, deadline_at = _allocation(5, overdue=False)
    return await _add(
        db,
        Participation(
            session_id=session.id,
            account_id=account.id,
            state=S.CONFIRMED.value,
            created_at=allocated_at,
            allocated_at=allocated_at,
            deadline_at=deadline_at,
            confirmed_at=allocated_at,
        ),
        [(None, S.HELD, R.BOOKED), (S.HELD, S.CONFIRMED, R.PAYMENT_CONFIRMED)],
    )


async def make_scope(db: AsyncSession, *, capacity: int = 2, window: Window = "open"):
    """An organizer, a published event and one published session."""
    organizer = await make_account(db, role=Role.ORGANIZER)
    event = await make_event(db, organizer)
    session = await make_session(db, event, capacity=capacity, window=window)
    return organizer, event, session
