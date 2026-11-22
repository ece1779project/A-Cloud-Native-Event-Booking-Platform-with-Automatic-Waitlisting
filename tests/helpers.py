"""Read-back helpers and shortcuts shared by the database tests. Rows are
built with ``tests.factories``; this module only reads them back or calls
the worker services with the default offer validity."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event_session import EventSession
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange
from app.schemas.realtime import SessionChange
from app.services.expiration import expire_session, promote_session

SessionFactory = async_sessionmaker[AsyncSession]

OFFER_VALIDITY = 600  # seconds; the OFFER_VALIDITY_SECONDS default


async def reload(
    session_factory: SessionFactory, participations: Iterable[Participation]
) -> list[Participation]:
    """Fresh copies of the rows, read in a new transaction."""
    async with session_factory.begin() as db:
        return [await db.get(Participation, p.id) for p in participations]


async def session_version(
    session_factory: SessionFactory, session_id: uuid.UUID
) -> int:
    async with session_factory.begin() as db:
        return (await db.get(EventSession, session_id)).version


async def latest_history(
    session_factory: SessionFactory, participation_id: uuid.UUID
) -> ParticipationChange:
    async with session_factory.begin() as db:
        return await db.scalar(
            select(ParticipationChange)
            .where(ParticipationChange.participation_id == participation_id)
            .order_by(ParticipationChange.change_no.desc())
            .limit(1)
        )


# An offer made while an older waiter of the same session was still waiting.
_FIFO_VIOLATIONS = text(
    """
    SELECT o.participation_id
    FROM participation_changes o
    JOIN participations p ON p.id = o.participation_id
    JOIN participations w ON w.session_id = p.session_id AND w.queue_seq < p.queue_seq
    JOIN participation_changes wj ON wj.participation_id = w.id AND wj.to_state = 'WAITLISTED'
    LEFT JOIN participation_changes wl
      ON wl.participation_id = w.id AND wl.from_state = 'WAITLISTED'
    WHERE o.to_state = 'OFFERED'
      AND wj.occurred_at < o.occurred_at
      AND (wl.occurred_at IS NULL OR wl.occurred_at > o.occurred_at)
    """
)


async def fifo_violations(session_factory: SessionFactory) -> list[uuid.UUID]:
    """Offers that skipped an older waiter. Must always be empty."""
    async with session_factory() as db:
        return list(await db.scalars(_FIFO_VIOLATIONS))


async def expire(
    session_factory: SessionFactory, session_id: uuid.UUID
) -> SessionChange | None:
    return await expire_session(
        session_factory, session_id, offer_validity_seconds=OFFER_VALIDITY
    )


async def promote(
    session_factory: SessionFactory, session_id: uuid.UUID
) -> SessionChange | None:
    return await promote_session(
        session_factory, session_id, offer_validity_seconds=OFFER_VALIDITY
    )
