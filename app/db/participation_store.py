"""Participation reads and low-level writes (DATABASE_DESIGN Sections 3-5, 7).

Functions that change a session or append history assume the caller already
holds that session's lock from ``app.db.locks``.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Row, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import EventSession
from app.models.participation import (
    ACTIVE_STATES,
    Participation,
    ParticipationChange,
    ParticipationState,
)


@dataclass(frozen=True, slots=True)
class SessionCounts:
    held: int
    offered: int
    confirmed: int
    waiting: int

    @property
    def occupied(self) -> int:
        return self.held + self.offered + self.confirmed


async def session_counts(db: AsyncSession, session_id: uuid.UUID) -> SessionCounts:
    """Aggregate the four active states in one statement (one snapshot)."""
    state = Participation.state
    row = (
        await db.execute(
            select(
                func.count().filter(state == ParticipationState.HELD),
                func.count().filter(state == ParticipationState.OFFERED),
                func.count().filter(state == ParticipationState.CONFIRMED),
                func.count().filter(state == ParticipationState.WAITLISTED),
            ).where(Participation.session_id == session_id)
        )
    ).one()
    return SessionCounts(*row)


async def active_attempt(
    db: AsyncSession, session_id: uuid.UUID, account_id: uuid.UUID
) -> Participation | None:
    """The account's single active attempt in a session, if any."""
    return await db.scalar(
        select(Participation)
        .where(
            Participation.session_id == session_id,
            Participation.account_id == account_id,
            Participation.state.in_(ACTIVE_STATES),
        )
        .execution_options(populate_existing=True)
    )


async def queue_head(db: AsyncSession, session_id: uuid.UUID) -> Participation | None:
    """Lowest-sequence waiting entry. Never use SKIP LOCKED here (strict FIFO)."""
    return await db.scalar(
        select(Participation)
        .where(
            Participation.session_id == session_id,
            Participation.state == ParticipationState.WAITLISTED,
        )
        .order_by(Participation.queue_seq)
        .limit(1)
        .execution_options(populate_existing=True)
    )


async def waitlist_position(db: AsyncSession, participation: Participation) -> int | None:
    """1 + number of waiting rows ahead; None unless the attempt is WAITLISTED."""
    if participation.state != ParticipationState.WAITLISTED:
        return None
    ahead = await db.scalar(
        select(func.count()).where(
            Participation.session_id == participation.session_id,
            Participation.state == ParticipationState.WAITLISTED,
            Participation.queue_seq < participation.queue_seq,
        )
    )
    return ahead + 1


async def next_queue_seq(db: AsyncSession, session: EventSession) -> int:
    """Reserve the next FIFO number. Caller must hold the session lock."""
    session.last_queue_seq += 1
    await db.flush()
    return session.last_queue_seq


async def bump_version(db: AsyncSession, session: EventSession) -> int:
    """Increment the session's change counter. Caller must hold the session lock.

    Call once in every transaction that changes the session's participations,
    capacity or status.
    """
    session.version += 1
    await db.flush()
    return session.version


async def record_change(
    db: AsyncSession,
    *,
    participation_id: uuid.UUID,
    from_state: ParticipationState | None,
    to_state: ParticipationState,
    reason_code: str,
    occurred_at: datetime,
    correlation_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> ParticipationChange:
    """Append the next numbered transition. Caller must hold the session lock,
    which serializes all transitions of the attempt."""
    last = await db.scalar(
        select(func.coalesce(func.max(ParticipationChange.change_no), 0)).where(
            ParticipationChange.participation_id == participation_id
        )
    )
    change = ParticipationChange(
        participation_id=participation_id,
        change_no=last + 1,
        from_state=from_state,
        to_state=to_state,
        occurred_at=occurred_at,
        reason_code=reason_code,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )
    db.add(change)
    await db.flush()
    return change


_CAPACITY_VIOLATIONS = text(
    """
    SELECT s.id, s.capacity, count(p.id) AS occupied
    FROM event_sessions s
    JOIN participations p
      ON p.session_id = s.id AND p.state IN ('HELD', 'OFFERED', 'CONFIRMED')
    GROUP BY s.id
    HAVING count(p.id) > s.capacity
    """
)


async def capacity_violations(db: AsyncSession) -> list[Row]:
    """The design's Section 7 check. Must always return no rows; workers export
    its length as a metric and tests run it after every round."""
    return list((await db.execute(_CAPACITY_VIOLATIONS)).all())
