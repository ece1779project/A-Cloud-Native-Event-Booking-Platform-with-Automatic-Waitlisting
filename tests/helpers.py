"""Read-back helpers and shortcuts shared by the database tests. Rows are
built with ``tests.factories``; this module only reads them back or calls
the worker services with the default offer validity."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
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
