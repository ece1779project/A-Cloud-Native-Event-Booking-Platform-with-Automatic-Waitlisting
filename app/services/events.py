"""Event create, edit, publish, cancel and the public event queries, with
ownership checks.

Also the guards and helpers the session and participation services share:
``can_manage``, the visibility and cancellation checks, ``session_view``
and ``session_change``.

Design: TECHNICAL_DESIGN.md Section 8.3.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode
from app.db.engine import db_now
from app.db.locks import RowMissing, lock_event_scope
from app.db.participation_store import (
    apply_transition,
    attempts_in_states,
    bump_version,
    session_counts,
)
from app.domain.enums import (
    EndReason,
    ManagementActionType,
    ParticipationState,
    Role,
)
from app.domain.enums import (
    PublicationStatus as PS,
)
from app.domain.timing import registration_status
from app.domain.transitions import ACTIVE_STATES
from app.models.account import Account
from app.models.event import Event
from app.models.event_session import EventSession
from app.schemas.common import Page, decode_cursor, encode_cursor
from app.schemas.events import EventCreate, EventPatch, EventView
from app.schemas.realtime import ChangeCause, ParticipationTransition, SessionChange
from app.schemas.sessions import SessionView
from app.services.audit import record_action

Factory = async_sessionmaker[AsyncSession]


# ---- Guards shared by the event, session and participation services ----


def can_manage(actor: Account | None, event: Event) -> bool:
    """An administrator, or the organizer who owns the event."""
    if actor is None:
        return False
    if actor.role == Role.ADMIN:
        return True
    return actor.role == Role.ORGANIZER and event.organizer_id == actor.id


def ensure_visible(
    viewer: Account | None, event: Event, session: EventSession | None = None
) -> None:
    """A draft the caller cannot manage does not exist for them: 404, on
    management routes too, so a response never reveals the draft."""
    if can_manage(viewer, event):
        return
    if event.status == PS.DRAFT or (session is not None and session.status == PS.DRAFT):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND)


def ensure_can_manage(
    actor: Account, event: Event, session: EventSession | None = None
) -> None:
    """404 for an invisible draft first, then 403 for someone else's event."""
    ensure_visible(actor, event, session)
    if not can_manage(actor, event):
        raise AppError(ErrorCode.FORBIDDEN)


def ensure_not_cancelled(event: Event, session: EventSession | None = None) -> None:
    if event.status == PS.CANCELLED:
        raise AppError(ErrorCode.EVENT_CANCELLED)
    if session is not None and session.status == PS.CANCELLED:
        raise AppError(ErrorCode.SESSION_CANCELLED)


def reject_nulls(changes: dict) -> None:
    """PATCH may omit a field but may not null it: every column is NOT NULL."""
    nulls = sorted(k for k, v in changes.items() if v is None)
    if nulls:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Fields cannot be set to null.",
            details={"fields": nulls},
        )


async def session_view(db: AsyncSession, s: EventSession, now: datetime) -> SessionView:
    c = await session_counts(db, s.id)
    return SessionView(
        id=s.id,
        event_id=s.event_id,
        name=s.name,
        status=s.status,
        time_zone=s.time_zone,
        registration_opens_at=s.registration_opens_at,
        registration_closes_at=s.registration_closes_at,
        starts_at=s.starts_at,
        ends_at=s.ends_at,
        capacity=s.capacity,
        occupied_count=c.occupied,
        available_count=max(0, s.capacity - c.occupied),
        waitlist_count=c.waiting,
        registration_status=registration_status(
            s.registration_opens_at, s.registration_closes_at, now
        ),
        cancelled_at=s.cancelled_at,
        cancellation_reason=s.cancellation_reason,
        version=s.version,
    )


async def session_change(
    db: AsyncSession,
    session: EventSession,
    cause: ChangeCause,
    transitions: Iterable[ParticipationTransition] = (),
) -> SessionChange:
    """Bump the version once and describe the commit. For management commands,
    which change the session even when no participation changes; attendee
    commands use ``expiration.build_session_change``."""
    return SessionChange(
        event_id=session.event_id,
        session_id=session.id,
        version=await bump_version(db, session),
        cause=cause,
        transitions=tuple(transitions),
    )


async def terminalize_active(
    db: AsyncSession,
    session: EventSession,
    *,
    reason: EndReason,
    now: datetime,
    correlation_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> list[ParticipationTransition]:
    """End every active attempt of a session being cancelled. Never promotes."""
    transitions = []
    for p in await attempts_in_states(db, session.id, ACTIVE_STATES):
        await apply_transition(
            db,
            p,
            ParticipationState.CANCELLED,
            reason_code=reason,
            now=now,
            correlation_id=correlation_id,
            actor_id=actor_id,
        )
        transitions.append(ParticipationTransition.of(p))
    return transitions


async def _lock_event(
    db: AsyncSession, event_id: uuid.UUID, *, all_sessions: bool = False
):
    try:
        return await lock_event_scope(
            db, event_id, exclusive_event=True, all_sessions=all_sessions
        )
    except RowMissing as exc:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND) from exc


# ---- Commands ----


async def create_event(
    factory: Factory, actor: Account, body: EventCreate
) -> EventView:
    async with factory.begin() as db:
        # The owner is the caller, never a request field. New events are drafts.
        event = Event(organizer_id=actor.id, **body.model_dump())
        db.add(event)
        await db.flush()
        await db.refresh(event)
    return EventView.of(event)


async def patch_event(
    factory: Factory, actor: Account, event_id: uuid.UUID, body: EventPatch
) -> EventView:
    changes = body.model_dump(exclude_unset=True)
    reject_nulls(changes)
    async with factory.begin() as db:
        scope = await _lock_event(db, event_id)
        event = scope.event
        ensure_can_manage(actor, event)
        ensure_not_cancelled(event)
        for k, v in changes.items():
            setattr(event, k, v)
    # TODO(realtime): management.changed event_updated with empty sessions (10.5).
    return EventView.of(event)


async def publish_event(
    factory: Factory, actor: Account, event_id: uuid.UUID
) -> tuple[EventView, list[SessionChange]]:
    """Publish every valid draft session and, if still a draft, the event.

    A valid draft session is one whose registration close is later than the
    database time. Invalid drafts stay ``DRAFT``. A draft event without a
    valid session is ``409``; a published event with none is ``200`` unchanged.
    """
    async with factory.begin() as db:
        scope = await _lock_event(db, event_id, all_sessions=True)
        event, now = scope.event, scope.now
        ensure_can_manage(actor, event)
        ensure_not_cancelled(event)

        valid = [
            s
            for s in scope.sessions.values()
            if s.status == PS.DRAFT and s.registration_closes_at > now
        ]
        event_was_draft = event.status == PS.DRAFT
        if event_was_draft and not valid:
            raise AppError(
                ErrorCode.INVALID_STATE_TRANSITION,
                "Publication requires at least one session whose registration has not closed.",
            )
        event.status = PS.PUBLISHED.value
        cause = (
            ChangeCause.EVENT_PUBLISHED
            if event_was_draft
            else ChangeCause.SESSION_PUBLISHED
        )
        changes = []
        for s in valid:
            s.status = PS.PUBLISHED.value
            changes.append(await session_change(db, s, cause))
    return EventView.of(event), changes


async def cancel_event(
    factory: Factory, actor: Account, event_id: uuid.UUID, reason: str
) -> tuple[EventView, list[SessionChange]]:
    """One transaction: the event, every session, every active attempt, one
    audit row. Repeating it returns the cancelled event unchanged."""
    correlation_id = uuid.uuid7()
    async with factory.begin() as db:
        scope = await _lock_event(db, event_id, all_sessions=True)
        event, now = scope.event, scope.now
        ensure_can_manage(actor, event)
        if event.status == PS.CANCELLED:
            return EventView.of(event), []

        event.status, event.cancelled_at, event.cancellation_reason = (
            PS.CANCELLED.value,
            now,
            reason,
        )
        changes = []
        for s in scope.sessions.values():
            changed = False
            if s.status != PS.CANCELLED:
                s.status, s.cancelled_at, s.cancellation_reason = (
                    PS.CANCELLED.value,
                    now,
                    reason,
                )
                changed = True
            transitions = await terminalize_active(
                db,
                s,
                reason=EndReason.EVENT_CANCELLED,
                now=now,
                correlation_id=correlation_id,
                actor_id=actor.id,
            )
            if changed or transitions:
                changes.append(
                    await session_change(
                        db, s, ChangeCause.EVENT_CANCELLED, transitions
                    )
                )
        await record_action(
            db,
            actor_id=actor.id,
            action=ManagementActionType.EVENT_CANCELLED,
            reason=reason,
            occurred_at=now,
            correlation_id=correlation_id,
            target_event_id=event.id,
        )
    return EventView.of(event), changes


# ---- Queries (no locks) ----


def _like_escape(q: str) -> str:
    """Make ``%``, ``_`` and ``\\`` match literally in ILIKE."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bad_cursor(exc: Exception) -> AppError:
    return AppError(ErrorCode.VALIDATION_ERROR, "Invalid cursor.")


async def list_events(
    factory: Factory, *, q: str | None, open_only: bool, cursor: str | None, limit: int
) -> Page[EventView]:
    """Published events by nearest upcoming published session start, then ID;
    events without an upcoming session last. Cursor: ``(next_start, id)``."""
    async with factory() as db:
        now = await db_now(db)
        ns = (
            select(
                EventSession.event_id,
                func.min(EventSession.starts_at).label("next_start"),
            )
            .where(EventSession.status == PS.PUBLISHED, EventSession.starts_at > now)
            .group_by(EventSession.event_id)
            .subquery()
        )
        stmt = (
            select(Event, ns.c.next_start)
            .outerjoin(ns, ns.c.event_id == Event.id)
            .where(Event.status == PS.PUBLISHED)
        )
        if q and q.strip():
            pat = f"%{_like_escape(q.strip())}%"
            stmt = stmt.where(
                or_(
                    Event.title.ilike(pat, escape="\\"),
                    Event.description.ilike(pat, escape="\\"),
                )
            )
        if open_only:
            stmt = stmt.where(
                exists().where(
                    EventSession.event_id == Event.id,
                    EventSession.status == PS.PUBLISHED,
                    EventSession.registration_opens_at <= now,
                    EventSession.registration_closes_at > now,
                )
            )
        if cursor:
            c = decode_cursor(cursor)
            try:
                cid = uuid.UUID(c["id"])
                cs = datetime.fromisoformat(c["s"]) if c.get("s") else None
            except (KeyError, ValueError, TypeError) as exc:
                raise _bad_cursor(exc) from exc
            if cs is None:
                stmt = stmt.where(ns.c.next_start.is_(None), Event.id > cid)
            else:
                stmt = stmt.where(
                    or_(
                        ns.c.next_start > cs,
                        and_(ns.c.next_start == cs, Event.id > cid),
                        ns.c.next_start.is_(None),
                    )
                )
        stmt = stmt.order_by(ns.c.next_start.asc().nulls_last(), Event.id).limit(
            limit + 1
        )
        rows = (await db.execute(stmt)).all()

    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last_event, last_start = page[-1]
        next_cursor = encode_cursor(
            {
                "s": last_start.isoformat() if last_start else None,
                "id": str(last_event.id),
            }
        )
    return Page(
        items=[EventView.of(e, next_start=s) for e, s in page], next_cursor=next_cursor
    )


async def list_my_events(
    factory: Factory, actor: Account, *, cursor: str | None, limit: int
) -> Page[EventView]:
    """Events the caller can manage, every status. Newest first, then ID."""
    stmt = select(Event)
    if actor.role != Role.ADMIN:
        stmt = stmt.where(Event.organizer_id == actor.id)
    if cursor:
        c = decode_cursor(cursor)
        try:
            cid, cc = uuid.UUID(c["id"]), datetime.fromisoformat(c["c"])
        except (KeyError, ValueError, TypeError) as exc:
            raise _bad_cursor(exc) from exc
        stmt = stmt.where(
            or_(Event.created_at < cc, and_(Event.created_at == cc, Event.id < cid))
        )
    stmt = stmt.order_by(Event.created_at.desc(), Event.id.desc()).limit(limit + 1)
    async with factory() as db:
        rows = list(await db.scalars(stmt))
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = encode_cursor(
            {"c": last.created_at.isoformat(), "id": str(last.id)}
        )
    return Page(items=[EventView.of(e) for e in page], next_cursor=next_cursor)


async def get_event(
    factory: Factory, viewer: Account | None, event_id: uuid.UUID
) -> EventView:
    async with factory() as db:
        event = await db.get(Event, event_id)
        if event is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
        ensure_visible(viewer, event)
        now = await db_now(db)
        sessions = await db.scalars(
            select(EventSession)
            .where(EventSession.event_id == event_id)
            .order_by(EventSession.starts_at, EventSession.id)
        )
        manager = can_manage(viewer, event)
        views = [
            await session_view(db, s, now)
            for s in sessions
            if manager or s.status != PS.DRAFT
        ]
    return EventView.of(event, sessions=views)
