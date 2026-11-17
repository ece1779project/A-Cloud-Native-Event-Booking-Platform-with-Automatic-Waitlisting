"""Scan 2: published sessions past registration close that still have
unconfirmed attempts (``HELD``, ``OFFERED`` or ``WAITLISTED``).

Design: TECHNICAL_DESIGN.md Section 11.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ParticipationState, PublicationStatus
from app.models.event_session import EventSession
from app.models.participation import Participation
from app.schemas.realtime import SessionChange
from app.services.expiration import close_session

_UNFINISHED = (
    ParticipationState.HELD,
    ParticipationState.OFFERED,
    ParticipationState.WAITLISTED,
)


@dataclass(frozen=True)
class ClosureScan:
    session_factory: async_sessionmaker[AsyncSession]
    name = "closure"

    async def find_candidates(self, limit: int) -> list[uuid.UUID]:
        unfinished = exists().where(
            Participation.session_id == EventSession.id,
            Participation.state.in_(_UNFINISHED),
        )
        async with self.session_factory() as db:
            return list(
                await db.scalars(
                    select(EventSession.id)
                    .where(
                        EventSession.status == PublicationStatus.PUBLISHED,
                        EventSession.registration_closes_at <= func.clock_timestamp(),
                        unfinished,
                    )
                    .order_by(EventSession.registration_closes_at)
                    .limit(limit)
                )
            )

    async def process(self, session_id: uuid.UUID) -> SessionChange | None:
        return await close_session(self.session_factory, session_id)
