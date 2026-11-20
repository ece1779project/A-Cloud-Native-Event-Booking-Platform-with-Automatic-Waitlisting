"""The two commands that create a participation: direct booking (``HELD``)
and explicit waitlist join (``WAITLISTED``).

Both run under the same session lock, so the capacity check and the insert
are one atomic step: ``occupied`` cannot change between them.

Design: TECHNICAL_DESIGN.md Section 7.2.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.db.locks import LockedScope, RowMissing, lock_session_scope
from app.db.participation_store import (
    active_attempt,
    next_queue_seq,
    record_change,
    session_counts,
)
from app.domain.enums import ParticipationState as S
from app.domain.enums import TransitionReason
from app.domain.timing import allocation_deadline
from app.models.account import Account
from app.models.participation import Participation
from app.schemas.realtime import ChangeCause, ParticipationTransition
from app.services.expiration import build_session_change
from app.services.idempotency import (
    CommandContext,
    CommandOutcome,
    parameter_hash,
    run_command,
)
from app.services.participations import (
    availability,
    ensure_enabled,
    registration_block,
    view_outcome,
)

Factory = async_sessionmaker[AsyncSession]


async def _lock(db: AsyncSession, session_id: uuid.UUID, account: Account) -> LockedScope:
    """Event FOR SHARE, session FOR UPDATE, account FOR SHARE, then the clock."""
    try:
        scope = await lock_session_scope(db, session_id, share_account_ids=[account.id])
    except RowMissing as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND) from exc
    ensure_enabled(scope, account.id)
    return scope


async def _insert(
    db: AsyncSession,
    scope: LockedScope,
    ctx: CommandContext,
    account: Account,
    p: Participation,
    reason: TransitionReason,
    cause: ChangeCause,
) -> CommandOutcome:
    db.add(p)
    await db.flush()
    await record_change(
        db,
        participation_id=p.id,
        from_state=None,
        to_state=S(p.state),
        reason_code=reason,
        occurred_at=scope.now,
        correlation_id=ctx.request_id,
        actor_id=account.id,
    )
    change = await build_session_change(db, scope, cause, [ParticipationTransition.of(p)])
    # 201 reaches the client only after run_command's transaction commits (FR-04.9).
    return await view_outcome(db, scope, p, status=201, code=p.state, change=change)


async def book(
    factory: Factory,
    settings: Settings,
    *,
    account: Account,
    session_id: uuid.UUID,
    request_id: uuid.UUID,
) -> CommandOutcome:
    operation = "BOOK"
    phash = parameter_hash(operation, session_id=session_id)

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        scope = await _lock(db, session_id, account)
        if (replayed := await ctx.recheck(db)) is not None:
            return replayed
        event, session, now = scope.event, scope.session, scope.now

        # 1. An existing active attempt comes back unchanged: no second seat,
        #    no change of queue order (FR-04.1, FR-06.5).
        if (p := await active_attempt(db, session.id, account.id)) is not None:
            return await view_outcome(db, scope, p, status=200, code="EXISTING_ACTIVE_PARTICIPATION")

        # 2. Published and inside the registration window, by the locked clock.
        if (code := registration_block(event, session, now)) is not None:
            return CommandOutcome.reject(code, session_version=session.version)

        # 3-5. A free seat AND an empty queue, or else WAITLIST_REQUIRED. Never
        #      jump the queue (FR-04.3) and never enqueue implicitly (FR-04.4).
        counts = await session_counts(db, session.id)
        if counts.waiting > 0 or counts.occupied >= session.capacity:
            return CommandOutcome.reject(
                ErrorCode.WAITLIST_REQUIRED,
                details=availability(counts, session),
                session_version=session.version,
            )

        p = Participation(
            session_id=session.id,
            account_id=account.id,
            state=S.HELD.value,
            created_at=now,  # the clock read under the lock, not transaction start
            allocated_at=now,
            deadline_at=allocation_deadline(
                now, settings.hold_validity_seconds, session.registration_closes_at
            ),
        )
        return await _insert(db, scope, ctx, account, p, TransitionReason.BOOKED, ChangeCause.BOOKING_HOLD)

    return await run_command(
        factory,
        account_id=account.id,
        request_id=request_id,
        operation=operation,
        phash=phash,
        execute=execute,
    )


async def join_waitlist(
    factory: Factory,
    settings: Settings,
    *,
    account: Account,
    session_id: uuid.UUID,
    request_id: uuid.UUID,
) -> CommandOutcome:
    operation = "JOIN_WAITLIST"
    phash = parameter_hash(operation, session_id=session_id)

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        scope = await _lock(db, session_id, account)
        if (replayed := await ctx.recheck(db)) is not None:
            return replayed
        event, session, now = scope.event, scope.session, scope.now

        if (p := await active_attempt(db, session.id, account.id)) is not None:
            return await view_outcome(db, scope, p, status=200, code="EXISTING_ACTIVE_PARTICIPATION")
        if (code := registration_block(event, session, now)) is not None:
            return CommandOutcome.reject(code, session_version=session.version)

        # The mirror image of book: a free seat and nobody waiting means book it (FR-06.2).
        counts = await session_counts(db, session.id)
        if counts.waiting == 0 and counts.occupied < session.capacity:
            return CommandOutcome.reject(
                ErrorCode.DIRECT_BOOKING_AVAILABLE,
                details=availability(counts, session),
                session_version=session.version,
            )

        # The counter on the locked session row is atomic; MAX(queue_seq)+1 is
        # not. Numbers are never reused, so leaving and rejoining goes to the tail.
        p = Participation(
            session_id=session.id,
            account_id=account.id,
            state=S.WAITLISTED.value,
            queue_seq=await next_queue_seq(db, session),
            created_at=now,
        )
        return await _insert(
            db, scope, ctx, account, p, TransitionReason.ENQUEUED, ChangeCause.WAITLIST_JOIN
        )

    return await run_command(
        factory,
        account_id=account.id,
        request_id=request_id,
        operation=operation,
        phash=phash,
        execute=execute,
    )
