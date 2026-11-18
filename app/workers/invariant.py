"""Scan 4: the capacity-invariant query, exported as the
``capacity_violations`` metric.

The whole check happens in ``find_candidates``; it returns no candidates, so
``process`` never runs.

Design: TECHNICAL_DESIGN.md Section 14; DATABASE_DESIGN.md Section 7.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.metrics import CAPACITY_VIOLATIONS
from app.db.participation_store import capacity_violations

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InvariantScan:
    session_factory: async_sessionmaker[AsyncSession]
    name = "invariant"

    async def find_candidates(self, limit: int) -> list[None]:
        async with self.session_factory() as db:
            violations = await capacity_violations(db)
        CAPACITY_VIOLATIONS.set(len(violations))
        for row in violations:
            log.error(
                "capacity violation: session %s has %s occupied of %s",
                row.id,
                row.occupied,
                row.capacity,
            )
        return []

    async def process(self, candidate: None) -> None:
        return None
