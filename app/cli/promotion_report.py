"""Promotion latency report: ``python -m app.cli.promotion_report
[--since ISO-8601] [--session UUID]``.

Reads ``participation_changes`` and measures, per PRD Section 2, the time from
a freed seat to the waitlist offer that took it:

- ``expiration``: from the hold or offer deadline (``HOLD_EXPIRED``,
  ``OFFER_EXPIRED``).
- ``cancellation``: from the commit of a cancel, decline or administrator
  cancel.

A release and its offer share a correlation ID. Within one ID, releases in
freed-time order are paired with offers in queue order, the same rule the
expiry worker uses for ``waitlist_promotion_latency_seconds``. Offers without
a release (a capacity increase, the promotion scan) and releases without an
offer (no waiter, registration closed, the 20-offer limit) are only counted.

Read-only. Design: TECHNICAL_DESIGN.md Section 14.
"""

import argparse
import asyncio
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.engine import create_engine, create_session_factory
from app.domain.enums import EndReason, ParticipationState
from app.domain.transitions import OCCUPYING_STATES
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange

CATEGORY = {
    EndReason.HOLD_EXPIRED: "expiration",
    EndReason.OFFER_EXPIRED: "expiration",
    EndReason.USER_CANCELLED: "cancellation",
    EndReason.OFFER_DECLINED: "cancellation",
    EndReason.ADMIN_CANCELLED: "cancellation",
}
MIN_OBSERVATIONS = 100  # PRD Section 2
P95_TARGET_SECONDS = 5.0


@dataclass
class LatencyReport:
    seconds: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    unpaired_offers: int = 0
    unpaired_releases: int = 0


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[math.ceil(q * len(ordered)) - 1]


async def promotion_latencies(
    db: AsyncSession,
    *,
    since: datetime | None = None,
    session_id: uuid.UUID | None = None,
) -> LatencyReport:
    change, p = ParticipationChange, Participation

    def scoped(stmt):
        stmt = stmt.join(p, p.id == change.participation_id)
        if since is not None:
            stmt = stmt.where(change.occurred_at >= since)
        if session_id is not None:
            stmt = stmt.where(p.session_id == session_id)
        return stmt

    releases = await db.execute(
        scoped(select(change.correlation_id, change.reason_code, change.occurred_at, p.deadline_at))
        .where(change.from_state.in_(OCCUPYING_STATES), change.reason_code.in_(CATEGORY))
    )
    offers = await db.execute(
        scoped(select(change.correlation_id, change.occurred_at, p.queue_seq))
        .where(change.to_state == ParticipationState.OFFERED)
    )

    freed: dict[uuid.UUID, list[tuple[datetime, str]]] = defaultdict(list)
    for cid, reason, occurred_at, deadline_at in releases:
        category = CATEGORY[reason]
        freed[cid].append((deadline_at if category == "expiration" else occurred_at, category))
    offered: dict[uuid.UUID, list[tuple[int, datetime]]] = defaultdict(list)
    for cid, occurred_at, queue_seq in offers:
        offered[cid].append((queue_seq, occurred_at))

    report = LatencyReport()
    for cid in freed.keys() | offered.keys():
        seats, taken = sorted(freed[cid]), sorted(offered[cid])
        for (freed_at, category), (_, offered_at) in zip(seats, taken, strict=False):
            report.seconds[category].append((offered_at - freed_at).total_seconds())
        report.unpaired_releases += max(0, len(seats) - len(taken))
        report.unpaired_offers += max(0, len(taken) - len(seats))
    return report


def format_report(report: LatencyReport) -> str:
    lines = []
    for category in ("expiration", "cancellation"):
        values = report.seconds[category]
        if not values:
            lines.append(f"{category}: n=0")
            continue
        p95 = percentile(values, 0.95)
        met = len(values) >= MIN_OBSERVATIONS and p95 <= P95_TARGET_SECONDS
        lines.append(
            f"{category}: n={len(values)} p50={percentile(values, 0.5):.3f}s "
            f"p95={p95:.3f}s max={max(values):.3f}s target_met={met}"
        )
    lines.append(
        f"unpaired: offers={report.unpaired_offers} releases={report.unpaired_releases}"
    )
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=datetime.fromisoformat)
    parser.add_argument("--session", type=uuid.UUID)
    args = parser.parse_args(argv)

    engine = create_engine(get_settings())
    try:
        async with create_session_factory(engine)() as db:
            report = await promotion_latencies(db, since=args.since, session_id=args.session)
    finally:
        await engine.dispose()
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
