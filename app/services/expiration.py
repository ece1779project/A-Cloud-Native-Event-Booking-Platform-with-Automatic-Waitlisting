"""Expiry of overdue holds and offers and the registration-closure
transitions. Shared by attendee commands that meet an overdue attempt and
by the worker scans.

Two kinds of function live here:

- Steps (``expire_if_overdue``, ``end_waiting_if_closed``, ``expire_due``,
  ``close_registration``) run inside the caller's transaction. They use the
  scope's database time, record a null actor, and never commit or bump the
  version.
- Transaction owners (``expire_session``, ``close_session``,
  ``promote_session``) are what the worker scans call. Each one locks a
  session, runs its steps, bumps the version once if anything changed, and
  returns the committed ``SessionChange`` or ``None``.

Design: TECHNICAL_DESIGN.md Sections 7.3 and 11.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.locks import LockedScope, lock_session_scope
from app.db.participation_store import (
    attempts_in_states,
    bump_version,
    due_attempts,
    transition,
)
from app.domain.enums import EndReason, ParticipationState
from app.models.participation import Participation
from app.schemas.realtime import ChangeCause, ParticipationTransition, SessionChange
from app.services.promotion import promote_waiters

S = ParticipationState

_EXPIRY_REASON = {S.HELD: EndReason.HOLD_EXPIRED, S.OFFERED: EndReason.OFFER_EXPIRED}


# Steps


async def expire_if_overdue(
    db: AsyncSession,
    scope: LockedScope,
    participation: Participation,
    *,
    correlation_id: uuid.UUID,
) -> list[ParticipationTransition]:
    """A ``HELD`` or ``OFFERED`` attempt at or past its deadline becomes
    ``EXPIRED``. Commands on an existing participation call this first."""
    state = S(participation.state)
    if state not in _EXPIRY_REASON or scope.now < participation.deadline_at:
        return []
    await transition(
        db,
        participation,
        S.EXPIRED,
        reason_code=_EXPIRY_REASON[state],
        now=scope.now,
        correlation_id=correlation_id,
        actor_id=None,
    )
    return [ParticipationTransition.of(participation)]


async def end_waiting_if_closed(
    db: AsyncSession,
    scope: LockedScope,
    participation: Participation,
    *,
    correlation_id: uuid.UUID,
) -> list[ParticipationTransition]:
    """A ``WAITLISTED`` entry at or after registration close becomes
    ``CANCELLED`` with ``REGISTRATION_CLOSED``."""
    if (
        participation.state != S.WAITLISTED
        or scope.now < scope.session.registration_closes_at
    ):
        return []
    await transition(
        db,
        participation,
        S.CANCELLED,
        reason_code=EndReason.REGISTRATION_CLOSED,
        now=scope.now,
        correlation_id=correlation_id,
        actor_id=None,
    )
    return [ParticipationTransition.of(participation)]


async def expire_due(
    db: AsyncSession, scope: LockedScope, *, correlation_id: uuid.UUID
) -> list[ParticipationTransition]:
    """Expire every overdue hold and offer of the locked session."""
    changes: list[ParticipationTransition] = []
    for participation in await due_attempts(db, scope.session.id, scope.now):
        changes += await expire_if_overdue(
            db, scope, participation, correlation_id=correlation_id
        )
    return changes


async def close_registration(
    db: AsyncSession, scope: LockedScope, *, correlation_id: uuid.UUID
) -> list[ParticipationTransition]:
    """After close: holds and offers expire (their deadlines are capped at the
    close), waiters end with ``REGISTRATION_CLOSED``, confirmed bookings stay.
    Never promotes. Does nothing before the close."""
    if scope.now < scope.session.registration_closes_at:
        return []
    changes: list[ParticipationTransition] = []
    rows = await attempts_in_states(
        db, scope.session.id, (S.HELD, S.OFFERED, S.WAITLISTED)
    )
    for participation in rows:
        changes += await expire_if_overdue(
            db, scope, participation, correlation_id=correlation_id
        )
        changes += await end_waiting_if_closed(
            db, scope, participation, correlation_id=correlation_id
        )
    return changes


# Transaction owners


async def build_session_change(
    db: AsyncSession,
    scope: LockedScope,
    cause: ChangeCause,
    transitions: list[ParticipationTransition],
) -> SessionChange | None:
    """Bump the version once and describe the commit, or return ``None`` and
    leave the version alone when no participation changed. Call last, just
    before the transaction commits."""
    if not transitions:
        return None
    return SessionChange(
        event_id=scope.event.id,
        session_id=scope.session.id,
        version=await bump_version(db, scope.session),
        cause=cause,
        transitions=tuple(transitions),
    )


async def expire_session(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    *,
    offer_validity_seconds: int,
) -> SessionChange | None:
    """Expire the session's overdue attempts and offer the freed seats, in one
    transaction. ``None`` when nothing was due (a stale candidate)."""
    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session_id)
        correlation_id = uuid.uuid7()
        changes = await expire_due(db, scope, correlation_id=correlation_id)
        if changes:
            changes += await promote_waiters(
                db,
                scope,
                offer_validity_seconds=offer_validity_seconds,
                correlation_id=correlation_id,
            )
        return await build_session_change(db, scope, ChangeCause.EXPIRATION, changes)


async def close_session(
    session_factory: async_sessionmaker[AsyncSession], session_id: uuid.UUID
) -> SessionChange | None:
    """Apply registration closure to one session."""
    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session_id)
        changes = await close_registration(db, scope, correlation_id=uuid.uuid7())
        return await build_session_change(
            db, scope, ChangeCause.REGISTRATION_CLOSED, changes
        )


async def promote_session(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    *,
    offer_validity_seconds: int,
) -> SessionChange | None:
    """The promotion scan's transaction: offer any free seats to waiters."""
    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session_id)
        changes = await promote_waiters(
            db,
            scope,
            offer_validity_seconds=offer_validity_seconds,
            correlation_id=uuid.uuid7(),
        )
        return await build_session_change(db, scope, ChangeCause.PROMOTION, changes)
