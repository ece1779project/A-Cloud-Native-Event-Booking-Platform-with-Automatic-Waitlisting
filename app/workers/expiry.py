"""Scan 1: due ``HELD`` and ``OFFERED`` rows.

Candidates are session IDs, oldest due deadline first. Each one is handled by
``expire_session``, which expires every due row of the session and offers the
freed seats in one transaction. The same read sets the backlog gauges.

Design: TECHNICAL_DESIGN.md Section 11.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.metrics import WORKER_OLDEST_OVERDUE, WORKER_OVERDUE_ITEMS
from app.domain.enums import ParticipationState
from app.models.participation import Participation
from app.schemas.realtime import SessionChange
from app.services.expiration import expire_session

_DUE = Participation.state.in_(
    (ParticipationState.HELD, ParticipationState.OFFERED)
) & (Participation.deadline_at <= func.clock_timestamp())


@dataclass(frozen=True)
class ExpiryScan:
    session_factory: async_sessionmaker[AsyncSession]
    offer_validity_seconds: int
    name = "expiry"

    async def find_candidates(self, limit: int) -> list[uuid.UUID]:
        async with self.session_factory() as db:
            count, oldest_seconds = (
                await db.execute(
                    select(
                        func.count(),
                        func.extract(
                            "epoch",
                            func.clock_timestamp()
                            - func.min(Participation.deadline_at),
                        ),
                    ).where(_DUE)
                )
            ).one()
            WORKER_OVERDUE_ITEMS.set(count)
            WORKER_OLDEST_OVERDUE.set(oldest_seconds or 0)
            return list(
                await db.scalars(
                    select(Participation.session_id)
                    .where(_DUE)
                    .group_by(Participation.session_id)
                    .order_by(func.min(Participation.deadline_at))
                    .limit(limit)
                )
            )

    async def process(self, session_id: uuid.UUID) -> SessionChange | None:
        return await expire_session(
            self.session_factory,
            session_id,
            offer_validity_seconds=self.offer_validity_seconds,
        )
