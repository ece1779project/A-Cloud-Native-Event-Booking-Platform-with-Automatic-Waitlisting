"""Every row of the publication matrix (TECHNICAL_DESIGN 10.5) against the
``SessionChange`` values the real operation returns, since they are the
publisher's only input (Section 10.3).

Each case seeds rows with the factories, runs one real service call and
returns its changes. The single test checks them against ``CASES``: the
cause and transition reasons of every change, and one version bump per
changed session.
"""

import uuid

import pytest
from sqlalchemy import select

from app.domain.enums import PublicationStatus, Role
from app.models.event_session import EventSession
from app.schemas.realtime import ChangeCause as C
from app.schemas.realtime import SessionChange
from app.schemas.sessions import SessionPatch
from app.services import booking
from app.services import events as esvc
from app.services import participations as psvc
from app.services import sessions as ssvc
from app.services.expiration import close_session
from tests.factories import (
    make_account,
    make_confirmed,
    make_held,
    make_offered,
    make_scope,
    make_session,
    make_waiting,
)
from tests.helpers import expire, promote

pytestmark = pytest.mark.usefixtures("capacity_invariant")

Changes = list[SessionChange]


async def _command(command, factory, settings, account, p, **kw) -> Changes:
    outcome = await command(
        factory,
        settings,
        account=account,
        participation_id=p.id,
        request_id=uuid.uuid4(),
        **kw,
    )
    return outcome.changes


async def _hold_and_waiter(factory, *, overdue=False, disabled_head=False):
    """A full capacity-1 session: one hold, then one or two waiters."""
    async with factory.begin() as db:
        organizer, _, session = await make_scope(db, capacity=1)
        holder = await make_account(db)
        hold = await make_held(db, session, holder, overdue=overdue)
        head = await make_account(db, disabled=disabled_head)
        waiter = await make_waiting(db, session, head, 1)
        if disabled_head:
            await make_waiting(db, session, await make_account(db), 2)
    return organizer, session, holder, hold, head, waiter


# ---- Attendee commands ----


async def booking_hold(factory, settings) -> Changes:
    async with factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
    outcome = await booking.book(
        factory, settings, account=a, session_id=session.id, request_id=uuid.uuid4()
    )
    return outcome.changes


async def waitlist_join(factory, settings) -> Changes:
    async with factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_held(db, session, await make_account(db))
        a = await make_account(db)
    outcome = await booking.join_waitlist(
        factory, settings, account=a, session_id=session.id, request_id=uuid.uuid4()
    )
    return outcome.changes


async def confirmation(factory, settings) -> Changes:
    _, _, holder, hold, _, _ = await _hold_and_waiter(factory)
    return await _command(
        psvc.confirm, factory, settings, holder, hold, simulation_result="success"
    )


async def payment_failure(factory, settings) -> Changes:
    _, _, holder, hold, _, _ = await _hold_and_waiter(factory)
    return await _command(
        psvc.confirm, factory, settings, holder, hold, simulation_result="failure"
    )


async def overdue_confirmation(factory, settings) -> Changes:
    _, _, holder, hold, _, _ = await _hold_and_waiter(factory, overdue=True)
    return await _command(
        psvc.confirm, factory, settings, holder, hold, simulation_result="success"
    )


async def cancel(factory, settings) -> Changes:
    _, _, holder, hold, _, _ = await _hold_and_waiter(factory)
    return await _command(psvc.cancel, factory, settings, holder, hold)


async def cancel_past_a_disabled_head(factory, settings) -> Changes:
    _, _, holder, hold, _, _ = await _hold_and_waiter(factory, disabled_head=True)
    return await _command(psvc.cancel, factory, settings, holder, hold)


async def decline(factory, settings) -> Changes:
    async with factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        offer = await make_offered(db, session, a, 1)
        await make_waiting(db, session, await make_account(db), 2)
    return await _command(psvc.decline, factory, settings, a, offer)


async def leave(factory, settings) -> Changes:
    _, _, _, _, head, waiter = await _hold_and_waiter(factory)
    return await _command(psvc.leave, factory, settings, head, waiter)


async def admin_cancel(factory, settings) -> Changes:
    async with factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        booked = await make_confirmed(db, session, await make_account(db))
        await make_waiting(db, session, await make_account(db), 1)
        admin = await make_account(db, role=Role.ADMIN)
    outcome = await psvc.admin_cancel(
        factory,
        settings,
        admin=admin,
        participation_id=booked.id,
        reason="Duplicate registration",
        request_id=uuid.uuid4(),
    )
    return outcome.changes


# ---- Worker scans ----


async def expiration(factory, settings) -> Changes:
    _, session, *_ = await _hold_and_waiter(factory, overdue=True)
    return [await expire(factory, session.id)]


async def registration_closed(factory, settings) -> Changes:
    async with factory.begin() as db:
        _, _, session = await make_scope(db, capacity=3, window="closed")
        await make_held(db, session, await make_account(db), overdue=True)
        await make_offered(db, session, await make_account(db), 1, overdue=True)
        await make_waiting(db, session, await make_account(db), 2)
        await make_confirmed(db, session, await make_account(db))
    return [await close_session(factory, session.id)]


async def promotion_scan(factory, settings) -> Changes:
    async with factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_waiting(db, session, await make_account(db), 1)
    return [await promote(factory, session.id)]


async def stale_worker_candidate(factory, settings) -> Changes:
    """Nothing due, registration open, no free seat: all three scans pass."""
    _, session, *_ = await _hold_and_waiter(factory)
    results = [
        await expire(factory, session.id),
        await close_session(factory, session.id),
        await promote(factory, session.id),
    ]
    return [c for c in results if c is not None]


# ---- Management commands ----


async def capacity_increase(factory, settings) -> Changes:
    organizer, session, *_ = await _hold_and_waiter(factory)
    outcome = await ssvc.patch_session(
        factory,
        settings,
        organizer,
        session.id,
        SessionPatch(capacity=2),
        request_id=uuid.uuid4(),
    )
    return outcome.changes


async def session_published(factory, settings) -> Changes:
    async with factory.begin() as db:
        organizer, event, _ = await make_scope(db, status=PublicationStatus.DRAFT)
    outcome = await esvc.publish_event(
        factory, organizer, event.id, request_id=uuid.uuid4()
    )
    return outcome.changes


async def event_published(factory, settings) -> Changes:
    async with factory.begin() as db:
        organizer, event, _ = await make_scope(db, status=PublicationStatus.DRAFT)
        await make_session(db, event, status=PublicationStatus.DRAFT)
        event.status = PublicationStatus.DRAFT.value
    outcome = await esvc.publish_event(
        factory, organizer, event.id, request_id=uuid.uuid4()
    )
    return outcome.changes


async def session_cancelled(factory, settings) -> Changes:
    organizer, session, *_ = await _hold_and_waiter(factory)
    outcome = await ssvc.cancel_session(
        factory, organizer, session.id, "Room unavailable", request_id=uuid.uuid4()
    )
    return outcome.changes


async def event_cancelled(factory, settings) -> Changes:
    """Two sessions: one with a hold, one empty. Each gets its own change."""
    async with factory.begin() as db:
        organizer, event, session = await make_scope(db, capacity=1)
        await make_held(db, session, await make_account(db))
        await make_session(db, event)
    outcome = await esvc.cancel_event(
        factory, organizer, event.id, "Speaker ill", request_id=uuid.uuid4()
    )
    return outcome.changes


# One row per case: the expected (cause, transition reasons) of each change.
CASES = [
    (booking_hold, [(C.BOOKING_HOLD, ["hold_created"])]),
    (waitlist_join, [(C.WAITLIST_JOIN, ["waitlist_joined"])]),
    (confirmation, [(C.CONFIRMATION, ["confirmed"])]),
    (payment_failure, []),
    (overdue_confirmation, [(C.CONFIRMATION, ["expired", "offer_created"])]),
    (cancel, [(C.ATTENDEE_RELEASE, ["cancelled", "offer_created"])]),
    (
        cancel_past_a_disabled_head,
        [(C.ATTENDEE_RELEASE, ["cancelled", "cancelled", "offer_created"])],
    ),
    (decline, [(C.ATTENDEE_RELEASE, ["cancelled", "offer_created"])]),
    (leave, [(C.ATTENDEE_RELEASE, ["cancelled"])]),
    (admin_cancel, [(C.ADMIN_PARTICIPATION_CANCEL, ["cancelled", "offer_created"])]),
    (expiration, [(C.EXPIRATION, ["expired", "offer_created"])]),
    # Hold first, then offer and waiter in queue order; the confirmed row stays.
    (
        registration_closed,
        [(C.REGISTRATION_CLOSED, ["expired", "expired", "cancelled"])],
    ),
    (promotion_scan, [(C.PROMOTION, ["offer_created"])]),
    (stale_worker_candidate, []),
    (capacity_increase, [(C.CAPACITY_CHANGED, ["offer_created"])]),
    (session_published, [(C.SESSION_PUBLISHED, [])]),
    (event_published, [(C.EVENT_PUBLISHED, []), (C.EVENT_PUBLISHED, [])]),
    (session_cancelled, [(C.SESSION_CANCELLED, ["cancelled", "cancelled"])]),
    (event_cancelled, [(C.EVENT_CANCELLED, ["cancelled"]), (C.EVENT_CANCELLED, [])]),
]


@pytest.mark.parametrize(
    ("scenario", "expected"), CASES, ids=[c[0].__name__ for c in CASES]
)
async def test_session_changes_follow_the_publication_matrix(
    session_factory, settings, scenario, expected
):
    changes = await scenario(session_factory, settings)

    # Sorted only across sessions: the order of transitions inside a change matters.
    assert sorted(
        (c.cause, [t.reason for t in c.transitions]) for c in changes
    ) == sorted(expected)

    # Every factory session starts at version 0. Each session the operation
    # advanced has exactly one change, carrying its committed version 1.
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(EventSession.event_id, EventSession.id, EventSession.version)
            )
        ).all()
    bumped = sorted(
        (event_id, session_id, v) for event_id, session_id, v in rows if v != 0
    )
    assert sorted((c.event_id, c.session_id, c.version) for c in changes) == bumped
    assert all(c.version == 1 for c in changes)
