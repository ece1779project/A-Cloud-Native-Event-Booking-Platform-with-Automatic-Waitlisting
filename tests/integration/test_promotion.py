"""Promotion: promote_waiters inside a caller's transaction and the
promote_session transaction owner (TECHNICAL_DESIGN 7.4)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.locks import lock_session_scope
from app.domain.enums import EndReason, ParticipationState, PublicationStatus
from app.models.event_session import EventSession
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange
from app.schemas.realtime import ChangeCause
from app.services.expiration import promote_session
from app.services.promotion import promote_waiters
from tests.factories import (
    make_account,
    make_confirmed,
    make_event,
    make_scope,
    make_session,
    make_waiting,
)

pytestmark = pytest.mark.usefixtures("capacity_invariant")

S = ParticipationState
VALIDITY = 600


async def _waiters(db, session, count, *, disabled=()):
    """Waiters with queue numbers 1..count; numbers in ``disabled`` get a
    disabled account."""
    return [
        await make_waiting(
            db, session, await make_account(db, disabled=seq in disabled), seq
        )
        for seq in range(1, count + 1)
    ]


async def _load(session_factory, participations):
    async with session_factory.begin() as db:
        return [await db.get(Participation, p.id) for p in participations]


async def _version(session_factory, session_id):
    async with session_factory.begin() as db:
        return (await db.get(EventSession, session_id)).version


async def test_one_seat_goes_to_the_lowest_queue_number(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        # Insert out of order: the queue number decides, not insertion order.
        late = await make_waiting(db, session, await make_account(db), 2)
        early = await make_waiting(db, session, await make_account(db), 1)

    change = await promote_session(
        session_factory, session.id, offer_validity_seconds=VALIDITY
    )

    assert change.cause == ChangeCause.PROMOTION
    assert change.version == 1
    assert [(t.participation_id, t.reason) for t in change.transitions] == [
        (early.id, "offer_created")
    ]
    early, late = await _load(session_factory, [early, late])
    assert (early.state, late.state) == (S.OFFERED, S.WAITLISTED)


async def test_several_seats_go_out_in_queue_order(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=3)
        await make_confirmed(db, session, await make_account(db))
        waiters = await _waiters(db, session, 4)

    change = await promote_session(
        session_factory, session.id, offer_validity_seconds=VALIDITY
    )

    assert [t.participation_id for t in change.transitions] == [
        waiters[0].id,
        waiters[1].id,
    ]
    states = [p.state for p in await _load(session_factory, waiters)]
    assert states == [S.OFFERED, S.OFFERED, S.WAITLISTED, S.WAITLISTED]


async def test_disabled_head_is_ended_and_the_next_waiter_is_offered(
    session_factory,
):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        waiters = await _waiters(db, session, 2, disabled={1})

    change = await promote_session(
        session_factory, session.id, offer_validity_seconds=VALIDITY
    )

    assert [t.reason for t in change.transitions] == ["cancelled", "offer_created"]
    skipped, offered = await _load(session_factory, waiters)
    assert (skipped.state, skipped.end_reason) == (
        S.CANCELLED,
        EndReason.ACCOUNT_DISABLED,
    )
    assert offered.state == S.OFFERED


@pytest.mark.parametrize("case", ["full", "closed", "draft", "event_cancelled"])
async def test_nothing_happens_without_an_open_seat(session_factory, case):
    async with session_factory.begin() as db:
        organizer = await make_account(db)
        event = await make_event(db, organizer)
        session = await make_session(
            db,
            event,
            capacity=1,
            window="closed" if case == "closed" else "open",
            status=PublicationStatus.DRAFT
            if case == "draft"
            else PublicationStatus.PUBLISHED,
        )
        if case == "full":
            await make_confirmed(db, session, await make_account(db))
        if case == "event_cancelled":
            event.status = PublicationStatus.CANCELLED
            event.cancelled_at = datetime.now(UTC)
            event.cancellation_reason = "test"
        (waiter,) = await _waiters(db, session, 1)

    change = await promote_session(
        session_factory, session.id, offer_validity_seconds=VALIDITY
    )

    assert change is None
    assert await _version(session_factory, session.id) == 0
    assert (await _load(session_factory, [waiter]))[0].state == S.WAITLISTED


async def test_offer_deadline_is_capped_at_registration_close(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)  # closes in one hour
        (waiter,) = await _waiters(db, session, 1)

    await promote_session(session_factory, session.id, offer_validity_seconds=7200)

    (offered,) = await _load(session_factory, [waiter])
    assert offered.deadline_at == session.registration_closes_at


async def test_at_most_twenty_offers_per_transaction(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=25)
        waiters = await _waiters(db, session, 25)

    first = await promote_session(
        session_factory, session.id, offer_validity_seconds=VALIDITY
    )
    assert len(first.transitions) == 20
    states = [p.state for p in await _load(session_factory, waiters)]
    assert states == [S.OFFERED] * 20 + [S.WAITLISTED] * 5

    # The next scan picks up the remainder.
    second = await promote_session(
        session_factory, session.id, offer_validity_seconds=VALIDITY
    )
    assert len(second.transitions) == 5
    assert second.version == first.version + 1


async def test_history_records_a_system_promotion(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=2)
        waiters = await _waiters(db, session, 2)

    await promote_session(session_factory, session.id, offer_validity_seconds=VALIDITY)

    async with session_factory.begin() as db:
        rows = list(
            await db.scalars(
                select(ParticipationChange).where(
                    ParticipationChange.participation_id.in_([w.id for w in waiters]),
                    ParticipationChange.to_state == S.OFFERED,
                )
            )
        )
    assert len(rows) == 2
    assert {r.reason_code for r in rows} == {"PROMOTED"}
    assert {r.actor_id for r in rows} == {None}
    assert len({r.correlation_id for r in rows}) == 1


async def test_step_inside_a_caller_transaction_leaves_the_version(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        (waiter,) = await _waiters(db, session, 1)

    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session.id)
        changes = await promote_waiters(
            db,
            scope,
            offer_validity_seconds=VALIDITY,
            correlation_id=uuid.uuid4(),
        )
        assert [t.participation_id for t in changes] == [waiter.id]

    assert await _version(session_factory, session.id) == 0
