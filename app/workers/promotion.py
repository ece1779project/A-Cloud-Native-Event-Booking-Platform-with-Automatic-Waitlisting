"""Scan 3: open sessions that have both free capacity and waiters.

A safety net: commands promote in their own transactions, and this scan
catches seats left over by the 20-offer limit or by a missed call site.

Design: TECHNICAL_DESIGN.md Section 11.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ParticipationState, PublicationStatus
from app.domain.transitions import OCCUPYING_STATES
from app.models.event import Event
from app.models.event_session import EventSession
from app.models.participation import Participation
from app.schemas.realtime import SessionChange
from app.services.expiration import promote_session


@dataclass(frozen=True)
class PromotionScan:
    session_factory: async_sessionmaker[AsyncSession]
    offer_validity_seconds: int
    name = "promotion"

    async def find_candidates(self, limit: int) -> list[uuid.UUID]:
        now = func.clock_timestamp()
        has_waiter = exists().where(
            Participation.session_id == EventSession.id,
            Participation.state == ParticipationState.WAITLISTED,
        )
        occupied = (
            select(func.count())
            .where(
                Participation.session_id == EventSession.id,
                Participation.state.in_(OCCUPYING_STATES),
            )
            .scalar_subquery()
        )
        async with self.session_factory() as db:
            return list(
                await db.scalars(
                    select(EventSession.id)
                    .join(Event, Event.id == EventSession.event_id)
                    .where(
                        Event.status == PublicationStatus.PUBLISHED,
                        EventSession.status == PublicationStatus.PUBLISHED,
                        EventSession.registration_opens_at <= now,
                        EventSession.registration_closes_at > now,
                        has_waiter,
                        occupied < EventSession.capacity,
                    )
                    .order_by(EventSession.id)
                    .limit(limit)
                )
            )

    async def process(self, session_id: uuid.UUID) -> SessionChange | None:
        return await promote_session(
            self.session_factory,
            session_id,
            offer_validity_seconds=self.offer_validity_seconds,
        )
