"""Event-session create, edit, capacity change, cancel, the availability read
and the organizer roster snapshot.

Design: TECHNICAL_DESIGN.md Sections 7.4 and 8.3.
"""

import uuid

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.db.engine import db_now
from app.db.locks import LockedScope, RowMissing, lock_event_scope, lock_session_scope
from app.db.participation_store import session_counts
from app.domain.enums import (
    EndReason,
    ManagementActionType,
)
from app.domain.enums import (
    ParticipationState as S,
)
from app.domain.enums import (
    PublicationStatus as PS,
)
from app.domain.transitions import ACTIVE_STATES
from app.models.account import Account
from app.models.event import Event
from app.models.event_session import EventSession
from app.models.participation import Participation
from app.schemas.realtime import ChangeCause, SessionChange
from app.schemas.sessions import (
    SCHEDULE_FIELDS,
    TIME_FIELDS,
    RosterCounts,
    RosterEntry,
    RosterView,
    SessionCreate,
    SessionPatch,
    SessionView,
    times_in_order,
)
from app.services.audit import record_action
from app.services.events import (
    ensure_can_manage,
    ensure_not_cancelled,
    ensure_visible,
    reject_nulls,
    session_change,
    session_view,
    terminalize_active,
)
from app.services.idempotency import (
    CommandContext,
    CommandOutcome,
    management_outcome,
    run_management,
)
from app.services.promotion import promote_waiters

Factory = async_sessionmaker[AsyncSession]


async def _lock_session(db: AsyncSession, session_id: uuid.UUID) -> LockedScope:
    """Event FOR SHARE, then the session FOR UPDATE."""
    try:
        return await lock_session_scope(db, session_id)
    except RowMissing as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND) from exc


async def create_session(
    factory: Factory,
    actor: Account,
    event_id: uuid.UUID,
    body: SessionCreate,
    *,
    request_id: uuid.UUID,
) -> CommandOutcome:
    """Every new session starts as ``DRAFT``, even on a published event;
    ``POST /events/{id}/publish`` is the only route that publishes it."""

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        try:
            # Exclusive event lock: no concurrent event cancellation or publication.
            scope = await lock_event_scope(db, event_id, exclusive_event=True)
        except RowMissing as exc:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND) from exc
        ensure_can_manage(actor, scope.event)
        ensure_not_cancelled(scope.event)
        session = EventSession(
            event_id=event_id, status=PS.DRAFT.value, **body.model_dump()
        )
        db.add(session)
        await db.flush()
        await db.refresh(session)
        view = await session_view(db, session, scope.now)
        return management_outcome(view, http_status=201, session_version=view.version)

    return await run_management(
        factory,
        actor_id=actor.id,
        request_id=request_id,
        operation="create_session",
        params={"event_id": event_id, **body.model_dump(mode="json")},
        execute=execute,
    )


async def _has_any_participation(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Terminal attempts count too: anyone who ever registered froze the schedule."""
    return await db.scalar(
        select(exists().where(Participation.session_id == session_id))
    )


async def patch_session(
    factory: Factory,
    settings: Settings,
    actor: Account,
    session_id: uuid.UUID,
    body: SessionPatch,
    *,
    request_id: uuid.UUID,
) -> CommandOutcome:
    changes = body.model_dump(exclude_unset=True)
    reject_nulls(changes)

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        scope = await _lock_session(db, session_id)
        event, session, now = scope.event, scope.session, scope.now
        ensure_can_manage(actor, event, session)
        ensure_not_cancelled(event, session)

        # FR-02.10: the database trigger also refuses this; checking first gives a clear 409.
        schedule_edit = any(
            f in changes and changes[f] != getattr(session, f) for f in SCHEDULE_FIELDS
        )
        if schedule_edit and await _has_any_participation(db, session.id):
            raise AppError(ErrorCode.SCHEDULE_FROZEN, session_version=session.version)

        merged = [changes.get(f, getattr(session, f)) for f in TIME_FIELDS]
        if not times_in_order(*merged):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "require registration_opens_at < registration_closes_at <= starts_at < ends_at",
            )

        old_capacity = session.capacity
        new_capacity = changes.get("capacity", old_capacity)
        if new_capacity < old_capacity:
            occupied = (await session_counts(db, session.id)).occupied
            if new_capacity < occupied:
                raise AppError(
                    ErrorCode.CAPACITY_BELOW_OCCUPIED,
                    details={"occupied_count": occupied, "capacity": old_capacity},
                    session_version=session.version,
                )

        for k, v in changes.items():
            setattr(session, k, v)
        await db.flush()

        result: list[SessionChange] = []
        if new_capacity != old_capacity:
            # FR-02.12: offer the new seats in this transaction, so a new
            # arrival cannot book ahead of the queue between two commits.
            transitions = []
            if new_capacity > old_capacity:
                transitions = await promote_waiters(
                    db,
                    scope,
                    offer_validity_seconds=settings.offer_validity_seconds,
                    correlation_id=uuid.uuid7(),
                )
            result.append(
                await session_change(
                    db, session, ChangeCause.CAPACITY_CHANGED, transitions
                )
            )
        view = await session_view(db, session, now)
        return management_outcome(view, session_version=view.version, changes=result)

    return await run_management(
        factory,
        actor_id=actor.id,
        request_id=request_id,
        operation="patch_session",
        params={
            "session_id": session_id,
            "changes": body.model_dump(mode="json", exclude_unset=True),
        },
        execute=execute,
    )


async def cancel_session(
    factory: Factory,
    actor: Account,
    session_id: uuid.UUID,
    reason: str,
    *,
    request_id: uuid.UUID,
) -> CommandOutcome:
    """Mark the session cancelled, end its active attempts, bump the version
    and write one audit row, in one transaction. Never promotes."""
    correlation_id = uuid.uuid7()

    async def execute(db: AsyncSession, ctx: CommandContext) -> CommandOutcome:
        scope = await _lock_session(db, session_id)
        event, session, now = scope.event, scope.session, scope.now
        ensure_can_manage(actor, event, session)
        if session.status == PS.CANCELLED:
            view = await session_view(db, session, now)
            return management_outcome(view, session_version=view.version)

        session.status, session.cancelled_at, session.cancellation_reason = (
            PS.CANCELLED.value,
            now,
            reason,
        )
        transitions = await terminalize_active(
            db,
            session,
            reason=EndReason.SESSION_CANCELLED,
            now=now,
            correlation_id=correlation_id,
            actor_id=actor.id,
        )
        change = await session_change(
            db, session, ChangeCause.SESSION_CANCELLED, transitions
        )
        await record_action(
            db,
            actor_id=actor.id,
            action=ManagementActionType.SESSION_CANCELLED,
            reason=reason,
            occurred_at=now,
            correlation_id=correlation_id,
            target_session_id=session.id,
        )
        view = await session_view(db, session, now)
        return management_outcome(view, session_version=view.version, changes=[change])

    return await run_management(
        factory,
        actor_id=actor.id,
        request_id=request_id,
        operation="cancel_session",
        params={"session_id": session_id, "reason": reason},
        execute=execute,
    )


async def get_session(
    factory: Factory, viewer: Account | None, session_id: uuid.UUID
) -> SessionView:
    async with factory() as db:
        s = await db.get(EventSession, session_id)
        if s is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
        ensure_visible(viewer, await db.get(Event, s.event_id), s)
        return await session_view(db, s, await db_now(db))


async def roster(factory: Factory, actor: Account, session_id: uuid.UUID) -> RosterView:
    """Counts and rows from one read-only ``REPEATABLE READ`` snapshot."""
    async with factory.begin() as db:
        # Must be the first statement: PostgreSQL takes the snapshot at the first query.
        await db.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        s = await db.get(EventSession, session_id)
        if s is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
        ensure_can_manage(actor, await db.get(Event, s.event_id), s)
        rows = (
            await db.execute(
                select(Participation, Account.display_name, Account.email)
                .join(Account, Account.id == Participation.account_id)
                .where(
                    Participation.session_id == session_id,
                    Participation.state.in_(ACTIVE_STATES),
                )
                .order_by(
                    Participation.queue_seq.nulls_first(),
                    Participation.created_at,
                    Participation.id,
                )
            )
        ).all()
        version = s.version

    groups: dict[str, list[RosterEntry]] = {
        st: [] for st in (S.HELD, S.OFFERED, S.CONFIRMED, S.WAITLISTED)
    }
    position = 0
    for p, name, email in rows:
        entry = RosterEntry(
            participation_id=p.id,
            account_id=p.account_id,
            display_name=name,
            email=email,
            state=p.state,
            deadline_at=p.deadline_at,
            confirmed_at=p.confirmed_at,
        )
        if p.state == S.WAITLISTED:
            # Rows arrive in queue order, so this is the position among waiters only.
            position += 1
            entry.position = position
        groups[p.state].append(entry)
    return RosterView(
        session_id=session_id,
        version=version,
        counts=RosterCounts(**{k.lower(): len(v) for k, v in groups.items()}),
        **{k.lower(): v for k, v in groups.items()},
    )
