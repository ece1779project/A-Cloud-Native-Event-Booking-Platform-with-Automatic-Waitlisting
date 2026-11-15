"""Minimal row builders for database tests. They bypass business rules on purpose."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, Role
from app.models.event import Event, EventSession, PublicationStatus
from app.models.participation import Participation, ParticipationState


async def make_account(
    db: AsyncSession, *, role: Role = Role.ATTENDEE, email: str | None = None
) -> Account:
    account = Account(
        email=email or f"{uuid.uuid4().hex[:12]}@example.com",
        display_name="Test User",
        password_hash="not-a-real-hash",
        role=role.value,
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
    db: AsyncSession, event: Event, *, capacity: int = 2, now: datetime | None = None
) -> EventSession:
    now = now or datetime.now(UTC)
    session = EventSession(
        event_id=event.id,
        name="Morning",
        time_zone="America/Toronto",
        status=PublicationStatus.PUBLISHED.value,
        registration_opens_at=now - timedelta(hours=1),
        registration_closes_at=now + timedelta(hours=1),
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=3),
        capacity=capacity,
    )
    db.add(session)
    await db.flush()
    return session


async def make_held(
    db: AsyncSession, session: EventSession, account: Account, *, minutes: int = 5
) -> Participation:
    now = datetime.now(UTC)
    p = Participation(
        session_id=session.id,
        account_id=account.id,
        state=ParticipationState.HELD.value,
        created_at=now,
        allocated_at=now,
        deadline_at=now + timedelta(minutes=minutes),
    )
    db.add(p)
    await db.flush()
    return p


async def make_waiting(
    db: AsyncSession, session: EventSession, account: Account, queue_seq: int
) -> Participation:
    p = Participation(
        session_id=session.id,
        account_id=account.id,
        state=ParticipationState.WAITLISTED.value,
        queue_seq=queue_seq,
    )
    db.add(p)
    await db.flush()
    return p


async def make_scope(db: AsyncSession, *, capacity: int = 2):
    """An organizer, a published event and one open session."""
    organizer = await make_account(db, role=Role.ORGANIZER)
    event = await make_event(db, organizer)
    session = await make_session(db, event, capacity=capacity)
    return organizer, event, session
