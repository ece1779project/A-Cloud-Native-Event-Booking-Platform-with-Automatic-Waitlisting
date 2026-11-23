"""Commands on an existing participation (confirm, cancel, decline, leave and
administrator cancel) and the attendee's participation queries.

Every command here has the same shape (TECHNICAL_DESIGN 7.3):

1. ``_lock_own`` (or ``_lock_any`` for the administrator): find the row's
   session, lock event and session, share-lock the caller's account.
2. ``ctx.recheck``: replay if the original request committed meanwhile.
3. ``_settle``: ``expire_if_overdue`` and ``end_waiting_if_closed``, so a
   late caller meets the outcome the worker would have written. If that
   freed a seat, promote, and answer with the terminal code.
4. Validate against the resulting state, apply the transition, promote if a
   seat was freed, and ``build_session_change`` last.

Decline and leave belong to Jianye and follow the same shape.

Design: TECHNICAL_DESIGN.md Sections 7.3, 7.4 and 8.4.
"""

import uuid
from typing import Literal

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.db.locks import LockedScope, RowMissing, lock_session_scope
from app.db.participation_store import (
    SessionCounts,
    apply_transition,
    waitlist_position,
)
from app.domain.enums import (
    EndReason,
    ManagementActionType,
    TransitionReason,
)
from app.domain.enums import (
    ParticipationState as S,
)
from app.domain.enums import (
    PublicationStatus as PS,
)
from app.domain.timing import RegistrationStatus, has_started, registration_status
from app.domain.transitions import ACTIVE_STATES, OCCUPYING_STATES
from app.models.account import Account
from app.models.event import Event
from app.models.event_session import EventSession
from app.models.participation import Participation
from app.schemas.common import Page, decode_cursor, encode_cursor
from app.schemas.participations import ParticipationView, SessionSummary
from app.schemas.realtime import ChangeCause, ParticipationTransition, SessionChange
from app.services.audit import record_action
from app.services.expiration import (
    build_session_change,
    end_waiting_if_closed,
    expire_if_overdue,
)
from app.services.idempotency import (
    CommandContext,
    CommandOutcome,
    parameter_hash,
    run_command,
)
from app.services.promotion import promote_waiters

Factory = async_sessionmaker[AsyncSession]


# ---- Views and checks shared with services/booking.py ----


async def build_view(
    db: AsyncSession,
    p: Participation,
    session: EventSession,
    event: Event,
    *,
    historical: bool = False,
) -> ParticipationView:
    return ParticipationView(
        id=p.id,
        session_id=p.session_id,
        account_id=p.account_id,
        state=p.state,
        # Computed on read: it changes whenever someone ahead leaves or is promoted.
        queue_position=await waitlist_position(db, p),
        created_at=p.created_at,
        allocated_at=p.allocated_at,
        deadline_at=p.deadline_at,
        confirmed_at=p.confirmed_at,
        ended_at=p.ended_at,
        end_reason=p.end_reason,
        display_label="Event cancelled" if event.status == PS.CANCELLED else None,
        historical=historical,
        session=SessionSummary(
            id=session.id,
            event_id=event.id,
            event_title=event.title,
            name=session.name,
            time_zone=session.time_zone,
            starts_at=session.starts_at,
            ends_at=session.ends_at,
            registration_closes_at=session.registration_closes_at,
            status=session.status,
            event_status=event.status,
        ),
        version=session.version,
    )


async def view_outcome(
    db: AsyncSession,
    scope: LockedScope,
    p: Participation,
    *,
    status: int,
    code: str,
    change: SessionChange | None = None,
) -> CommandOutcome:
    """A success outcome whose body is the participation view. Call after
    ``build_session_change`` so the view carries the new version."""
    view = await build_view(db, p, scope.session, scope.event)
    return CommandOutcome(
        http_status=status,
        result_code=code,
        # JSONB receipt: UUIDs and datetimes must already be strings.
        payload=view.model_dump(mode="json"),
        session_version=scope.session.version,
        participation_id=p.id,
        changes=[change] if change else [],
    )


def availability(counts: SessionCounts, session: EventSession) -> dict:
    """``details`` of WAITLIST_REQUIRED / DIRECT_BOOKING_AVAILABLE (openapi BookingConflict)."""
    return {
        "availability": {
            "capacity": session.capacity,
            "occupied_count": counts.occupied,
            "available_count": max(0, session.capacity - counts.occupied),
            "waitlist_count": counts.waiting,
        }
    }


def registration_block(event: Event, session: EventSession, now) -> ErrorCode | None:
    """Why a new attempt cannot be created, or ``None``. A draft raises 404
    (invisible, no receipt); the other reasons are stored rejections."""
    if event.status == PS.CANCELLED:
        return ErrorCode.EVENT_CANCELLED
    if session.status == PS.CANCELLED:
        return ErrorCode.SESSION_CANCELLED
    if event.status != PS.PUBLISHED or session.status != PS.PUBLISHED:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
    match registration_status(
        session.registration_opens_at, session.registration_closes_at, now
    ):
        case RegistrationStatus.NOT_OPEN:
            return ErrorCode.REGISTRATION_NOT_OPEN
        case RegistrationStatus.CLOSED:
            return ErrorCode.REGISTRATION_CLOSED
    return None


def ensure_enabled(scope: LockedScope, account_id: uuid.UUID) -> None:
    """Checked under the account's share lock, so a concurrent disablement
    either commits first (and we refuse) or waits for us."""
    if scope.accounts[account_id].disabled_at is not None:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)


# ---- Steps shared by the commands on an existing participation ----


async def _lock(
    db: AsyncSession, participation_id: uuid.UUID, caller: Account, *, owner_only: bool
) -> tuple[LockedScope, Participation]:
    stmt = select(Participation.session_id).where(Participation.id == participation_id)
    if owner_only:
        # Someone else's record is 404, not 403 (FR-01.8): never confirm it exists.
        stmt = stmt.where(Participation.account_id == caller.id)
    session_id = await db.scalar(stmt)
    if session_id is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
    try:
        scope = await lock_session_scope(db, session_id, share_account_ids=[caller.id])
    except RowMissing as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND) from exc
    ensure_enabled(scope, caller.id)
    # Read the row only after the session lock, which serializes every change to it.
    p = await db.get(Participation, participation_id, populate_existing=True)
    return scope, p


async def _settle(
    db: AsyncSession,
    scope: LockedScope,
    p: Participation,
    correlation_id: uuid.UUID,
    settings: Settings,
) -> list[ParticipationTransition]:
    """Apply what the worker would have: expiry, then closure for a waiter.
    An expired seat is offered to the queue in the same transaction."""
    transitions = await expire_if_overdue(db, scope, p, correlation_id=correlation_id)
    transitions += await end_waiting_if_closed(
        db, scope, p, correlation_id=correlation_id
    )
    if p.state == S.EXPIRED and transitions:
        transitions += await _promote(db, scope, correlation_id, settings)
    return transitions


async def _promote(
    db: AsyncSession, scope: LockedScope, correlation_id: uuid.UUID, settings: Settings
) -> list[ParticipationTransition]:
    return await promote_waiters(
        db,
        scope,
        offer_validity_seconds=settings.offer_validity_seconds,
        correlation_id=correlation_id,
    )


_TERMINAL_CODE = {
    EndReason.SESSION_CANCELLED: ErrorCode.SESSION_CANCELLED,
    EndReason.EVENT_CANCELLED: ErrorCode.EVENT_CANCELLED,
    EndReason.REGISTRATION_CLOSED: ErrorCode.REGISTRATION_CLOSED,
}


def terminal_code(p: Participation) -> ErrorCode:
    if p.state == S.EXPIRED:
        return ErrorCode.PARTICIPATION_EXPIRED
    return _TERMINAL_CODE.get(p.end_reason, ErrorCode.INVALID_STATE_TRANSITION)


async def _reject(
    db: AsyncSession,
    scope: LockedScope,
    p: Participation,
    code: ErrorCode,
    cause: ChangeCause,
    transitions: list[ParticipationTransition],
) -> CommandOutcome:
    """A stored rejection that still commits whatever ``_settle`` wrote."""
    change = await build_session_change(db, scope, cause, transitions)
    return CommandOutcome.reject(
        code,
        session_version=scope.session.version,
        participation_id=p.id,
        changes=[change] if change else [],
    )


# ---- Commands ----


async def confirm(
    factory: Factory,
    settings: Settings,
    *,
    account: Account,
    participation_id: uuid.UUID,
    simulation_result: Literal["success", "failure"],
    request_id: uuid.UUID,
) -> CommandOutcome:
    """Simulated payment for a hold or offer (FR-05, TECHNICAL_DESIGN 7.3).

    The deadline is judged with the database time read after the session
    lock, so a confirmation and the expiry worker can never both win. An
    overdue attempt is expired and its seat offered before answering
    ``PARTICIPATION_EXPIRED``; that rejection commits."""
    operation = "CONFIRM"
    # simulation_result is part of the hash: a retry after a failure needs a new key.
    phash = parameter_hash(
        operation,
        participation_id=participation_id,
        simulation_result=simulation_result,
    )
    cause = ChangeCause.CONFIRMATION

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        scope, p = await _lock(db, participation_id, account, owner_only=True)
        if (replayed := await ctx.recheck(db)) is not None:
            return replayed
        if settled := await _settle(db, scope, p, ctx.request_id, settings):
            return await _reject(db, scope, p, terminal_code(p), cause, settled)

        match p.state:
            case S.CONFIRMED:
                # Already confirmed, even under a new key: the unchanged result.
                return await view_outcome(db, scope, p, status=200, code="CONFIRMED")
            case S.CANCELLED | S.EXPIRED:
                return await _reject(db, scope, p, terminal_code(p), cause, [])
            case S.WAITLISTED:
                return await _reject(
                    db, scope, p, ErrorCode.INVALID_STATE_TRANSITION, cause, []
                )

        # HELD or OFFERED, still inside the deadline.
        if simulation_result == "failure":
            # Nothing changes: same state, same deadline, no version bump (FR-05.3).
            return await view_outcome(
                db, scope, p, status=200, code="PAYMENT_SIMULATION_FAILED"
            )
        await apply_transition(
            db,
            p,
            S.CONFIRMED,
            reason_code=TransitionReason.PAYMENT_CONFIRMED,
            now=scope.now,
            correlation_id=ctx.request_id,
            actor_id=account.id,
        )
        # Occupancy is unchanged, but the version still moves so clients see it.
        change = await build_session_change(
            db, scope, cause, [ParticipationTransition.of(p)]
        )
        return await view_outcome(
            db, scope, p, status=200, code="CONFIRMED", change=change
        )

    return await run_command(
        factory,
        account_id=account.id,
        request_id=request_id,
        operation=operation,
        phash=phash,
        execute=execute,
    )


async def cancel(
    factory: Factory,
    settings: Settings,
    *,
    account: Account,
    participation_id: uuid.UUID,
    request_id: uuid.UUID,
) -> CommandOutcome:
    """Attendee cancel of a hold or confirmed booking (FR-08). The freed seat
    goes to the queue in the same transaction. An offer is declined and a
    waitlist entry is left with their own commands, not this one."""
    operation = "CANCEL"
    phash = parameter_hash(operation, participation_id=participation_id)
    cause = ChangeCause.ATTENDEE_RELEASE

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        scope, p = await _lock(db, participation_id, account, owner_only=True)
        if (replayed := await ctx.recheck(db)) is not None:
            return replayed
        if settled := await _settle(db, scope, p, ctx.request_id, settings):
            return await _reject(db, scope, p, terminal_code(p), cause, settled)

        if p.state == S.CANCELLED:
            # Repeated cancel: unchanged result, the seat was released only once.
            return await view_outcome(db, scope, p, status=200, code="CANCELLED")
        if p.state == S.EXPIRED:
            return await _reject(
                db, scope, p, ErrorCode.PARTICIPATION_EXPIRED, cause, []
            )
        if has_started(scope.session.starts_at, scope.now):
            return await _reject(db, scope, p, ErrorCode.SESSION_STARTED, cause, [])
        if p.state not in (S.HELD, S.CONFIRMED):
            return await _reject(
                db, scope, p, ErrorCode.INVALID_STATE_TRANSITION, cause, []
            )

        await apply_transition(
            db,
            p,
            S.CANCELLED,
            reason_code=EndReason.USER_CANCELLED,
            now=scope.now,
            correlation_id=ctx.request_id,
            actor_id=account.id,
        )
        transitions = [ParticipationTransition.of(p)]
        transitions += await _promote(db, scope, ctx.request_id, settings)
        change = await build_session_change(db, scope, cause, transitions)
        return await view_outcome(
            db, scope, p, status=200, code="CANCELLED", change=change
        )

    return await run_command(
        factory,
        account_id=account.id,
        request_id=request_id,
        operation=operation,
        phash=phash,
        execute=execute,
    )


async def admin_cancel(
    factory: Factory,
    settings: Settings,
    *,
    admin: Account,
    participation_id: uuid.UUID,
    reason: str,
    request_id: uuid.UUID,
) -> CommandOutcome:
    """Administrator cancel of any active attempt, with an audit row whose
    correlation ID matches the history row."""
    operation = "ADMIN_CANCEL"
    phash = parameter_hash(operation, participation_id=participation_id, reason=reason)
    cause = ChangeCause.ADMIN_PARTICIPATION_CANCEL

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        scope, p = await _lock(db, participation_id, admin, owner_only=False)
        if (replayed := await ctx.recheck(db)) is not None:
            return replayed
        if settled := await _settle(db, scope, p, ctx.request_id, settings):
            return await _reject(db, scope, p, terminal_code(p), cause, settled)
        if p.state == S.CANCELLED:
            return await view_outcome(db, scope, p, status=200, code="CANCELLED")
        if p.state == S.EXPIRED:
            return await _reject(
                db, scope, p, ErrorCode.PARTICIPATION_EXPIRED, cause, []
            )

        freed_seat = S(p.state) in OCCUPYING_STATES
        await apply_transition(
            db,
            p,
            S.CANCELLED,
            reason_code=EndReason.ADMIN_CANCELLED,
            now=scope.now,
            correlation_id=ctx.request_id,
            actor_id=admin.id,
        )
        transitions = [ParticipationTransition.of(p)]
        if freed_seat:
            transitions += await _promote(db, scope, ctx.request_id, settings)
        await record_action(
            db,
            actor_id=admin.id,
            action=ManagementActionType.PARTICIPATION_CANCELLED,
            reason=reason,
            occurred_at=scope.now,
            correlation_id=ctx.request_id,
            target_participation_id=p.id,
        )
        change = await build_session_change(db, scope, cause, transitions)
        return await view_outcome(
            db, scope, p, status=200, code="CANCELLED", change=change
        )

    return await run_command(
        factory,
        account_id=admin.id,
        request_id=request_id,
        operation=operation,
        phash=phash,
        execute=execute,
    )


# ---- Queries (no locks) ----


def _historical(p: Participation) -> bool:
    return p.state not in ACTIVE_STATES


async def my_participation(
    factory: Factory, account: Account, session_id: uuid.UUID
) -> ParticipationView:
    """The active attempt, or else the latest terminal one marked historical."""
    async with factory() as db:
        s = await db.get(EventSession, session_id)
        if s is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
        event = await db.get(Event, s.event_id)
        p = await db.scalar(
            select(Participation)
            .where(
                Participation.session_id == session_id,
                Participation.account_id == account.id,
            )
            .order_by(
                case((Participation.state.in_(ACTIVE_STATES), 0), else_=1),
                Participation.created_at.desc(),
                Participation.id.desc(),
            )
            .limit(1)
        )
        if p is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
        return await build_view(db, p, s, event, historical=_historical(p))


async def my_participations(
    factory: Factory, account: Account, *, cursor: str | None, limit: int
) -> Page[ParticipationView]:
    """All attempts: HELD and OFFERED first by deadline (FR-11.4), then the
    rest newest first. The opaque cursor is an offset, because the sort key
    mixes a state rank with two different time columns."""
    offset = 0
    if cursor:
        try:
            offset = int(decode_cursor(cursor)["o"])
        except (KeyError, ValueError, TypeError) as exc:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid cursor.") from exc
        if offset < 0:
            raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid cursor.")
    async with factory() as db:
        rows = (
            await db.execute(
                select(Participation, EventSession, Event)
                .join(EventSession, EventSession.id == Participation.session_id)
                .join(Event, Event.id == EventSession.event_id)
                .where(Participation.account_id == account.id)
                .order_by(
                    case((Participation.state.in_((S.HELD, S.OFFERED)), 0), else_=1),
                    Participation.deadline_at.asc().nulls_last(),
                    Participation.created_at.desc(),
                    Participation.id.desc(),
                )
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        items = [
            await build_view(db, p, s, e, historical=_historical(p))
            for p, s, e in rows[:limit]
        ]
    next_cursor = encode_cursor({"o": offset + limit}) if len(rows) > limit else None
    return Page(items=items, next_cursor=next_cursor)


async def get_participation(
    factory: Factory, account: Account, participation_id: uuid.UUID
) -> ParticipationView:
    async with factory() as db:
        p = await db.get(Participation, participation_id)
        if p is None or p.account_id != account.id:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
        s = await db.get(EventSession, p.session_id)
        e = await db.get(Event, s.event_id)
        return await build_view(db, p, s, e, historical=_historical(p))
