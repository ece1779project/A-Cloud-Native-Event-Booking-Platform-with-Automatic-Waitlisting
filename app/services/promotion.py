"""The promote steps, run inside any transaction that frees a seat or raises
capacity and by the promotion scan. Always takes the true lowest
``queue_seq``; never uses ``SKIP LOCKED`` within a session.

``promote_waiters`` runs inside the caller's transaction: it never commits,
never bumps the session version and returns no ``SessionChange``.

Design: TECHNICAL_DESIGN.md Section 7.4; DATABASE_DESIGN.md Section 4.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MAX_OFFERS_PER_TRANSACTION
from app.db.locks import LockedScope, lock_queue_head_account
from app.db.participation_store import apply_transition, queue_head, session_counts
from app.domain.enums import (
    EndReason,
    ParticipationState,
    PublicationStatus,
    TransitionReason,
)
from app.models.event import Event
from app.models.event_session import EventSession
from app.schemas.realtime import ParticipationTransition

# TODO: switch both helpers to app.domain.timing once Jiawei merges it.


def _registration_open(event: Event, session: EventSession, now: datetime) -> bool:
    return (
        event.status == PublicationStatus.PUBLISHED
        and session.status == PublicationStatus.PUBLISHED
        and session.registration_opens_at <= now < session.registration_closes_at
    )


def _offer_deadline(
    now: datetime, validity_seconds: int, closes_at: datetime
) -> datetime:
    return min(now + timedelta(seconds=validity_seconds), closes_at)


async def promote_waiters(
    db: AsyncSession,
    scope: LockedScope,
    *,
    offer_validity_seconds: int,
    correlation_id: uuid.UUID,
) -> list[ParticipationTransition]:
    """Offer free seats to waiters in queue order, up to 20 offers.

    A disabled queue head ends as ``CANCELLED`` with ``ACCOUNT_DISABLED`` and
    promotion moves on. Every transition records a null actor. The caller
    must hold the session lock from ``lock_session_scope``.
    """
    event, session = scope.event, scope.session
    if not _registration_open(event, session, scope.now):
        return []

    free = session.capacity - (await session_counts(db, session.id)).occupied
    transitions: list[ParticipationTransition] = []
    offers = 0
    while free > 0 and offers < MAX_OFFERS_PER_TRANSACTION:
        head = await queue_head(db, session.id)
        if head is None:
            break
        # Recheck registration with the time read after this blocking lock.
        locked = await lock_queue_head_account(db, head.account_id)
        now = locked.now
        if not _registration_open(event, session, now):
            break

        if locked.account.disabled_at is not None:
            await apply_transition(
                db,
                head,
                ParticipationState.CANCELLED,
                reason_code=EndReason.ACCOUNT_DISABLED,
                now=now,
                correlation_id=correlation_id,
                actor_id=None,
            )
        else:
            await apply_transition(
                db,
                head,
                ParticipationState.OFFERED,
                reason_code=TransitionReason.PROMOTED,
                now=now,
                correlation_id=correlation_id,
                actor_id=None,
                deadline_at=_offer_deadline(
                    now, offer_validity_seconds, session.registration_closes_at
                ),
            )
            free -= 1
            offers += 1
        transitions.append(ParticipationTransition.of(head))
    return transitions
