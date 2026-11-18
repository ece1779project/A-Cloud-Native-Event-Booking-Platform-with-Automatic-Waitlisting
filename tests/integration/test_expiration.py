"""Expiry and registration closure: the steps and the expire_session and
close_session transaction owners (TECHNICAL_DESIGN 7.3 and 11)."""

import uuid

import pytest

from app.db.locks import lock_session_scope
from app.db.participation_store import queue_head
from app.domain.enums import EndReason, ParticipationState
from app.models.participation import Participation
from app.schemas.realtime import ChangeCause
from app.services.expiration import (
    close_session,
    end_waiting_if_closed,
    expire_if_overdue,
)
from tests.factories import (
    make_account,
    make_confirmed,
    make_held,
    make_offered,
    make_scope,
    make_waiting,
)
from tests.helpers import expire, latest_history, reload, session_version

pytestmark = pytest.mark.usefixtures("capacity_invariant")

S = ParticipationState


async def test_overdue_hold_and_offer_expire_with_their_reasons(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=3)
        hold = await make_held(db, session, await make_account(db), overdue=True)
        offer = await make_offered(db, session, await make_account(db), 1, overdue=True)
        fresh = await make_held(db, session, await make_account(db))

    change = await expire(session_factory, session.id)

    assert change.cause == ChangeCause.EXPIRATION
    assert {t.participation_id for t in change.transitions} == {hold.id, offer.id}
    assert {t.reason for t in change.transitions} == {"expired"}
    hold, offer, fresh = await reload(session_factory, [hold, offer, fresh])
    assert (hold.state, hold.end_reason) == (S.EXPIRED, EndReason.HOLD_EXPIRED)
    assert (offer.state, offer.end_reason) == (S.EXPIRED, EndReason.OFFER_EXPIRED)
    assert fresh.state == S.HELD


async def test_stale_candidate_is_a_no_op(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_held(db, session, await make_account(db))
        await make_waiting(db, session, await make_account(db), 1)

    assert await expire(session_factory, session.id) is None
    assert await session_version(session_factory, session.id) == 0


async def test_expiry_and_the_next_offer_share_one_commit(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        offer = await make_offered(db, session, await make_account(db), 1, overdue=True)
        waiter = await make_waiting(db, session, await make_account(db), 2)

    change = await expire(session_factory, session.id)

    assert [(t.participation_id, t.reason) for t in change.transitions] == [
        (offer.id, "expired"),
        (waiter.id, "offer_created"),
    ]
    assert change.version == 1
    offer, waiter = await reload(session_factory, [offer, waiter])
    assert (offer.state, waiter.state) == (S.EXPIRED, S.OFFERED)

    expired_row = await latest_history(session_factory, offer.id)
    offered_row = await latest_history(session_factory, waiter.id)
    assert expired_row.correlation_id == offered_row.correlation_id
    assert (expired_row.actor_id, offered_row.actor_id) == (None, None)

    # The expired offer does not rejoin the queue.
    async with session_factory.begin() as db:
        assert await queue_head(db, session.id) is None


async def test_closure_ends_each_state_and_issues_no_offer(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=5, window="closed")
        hold = await make_held(db, session, await make_account(db), overdue=True)
        offer = await make_offered(db, session, await make_account(db), 1, overdue=True)
        waiter = await make_waiting(db, session, await make_account(db), 2)
        confirmed = await make_confirmed(db, session, await make_account(db))

    change = await close_session(session_factory, session.id)

    assert change.cause == ChangeCause.REGISTRATION_CLOSED
    assert "offer_created" not in {t.reason for t in change.transitions}
    hold, offer, waiter, confirmed = await reload(
        session_factory, [hold, offer, waiter, confirmed]
    )
    assert (hold.state, hold.end_reason) == (S.EXPIRED, EndReason.HOLD_EXPIRED)
    assert (offer.state, offer.end_reason) == (S.EXPIRED, EndReason.OFFER_EXPIRED)
    assert (waiter.state, waiter.end_reason) == (
        S.CANCELLED,
        EndReason.REGISTRATION_CLOSED,
    )
    assert confirmed.state == S.CONFIRMED

    # A second run finds nothing left to do.
    assert await close_session(session_factory, session.id) is None
    assert await session_version(session_factory, session.id) == 1


async def test_closure_does_nothing_while_registration_is_open(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_waiting(db, session, await make_account(db), 1)

    assert await close_session(session_factory, session.id) is None


async def test_steps_leave_valid_rows_alone(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        hold = await make_held(db, session, await make_account(db))
        waiter = await make_waiting(db, session, await make_account(db), 1)

    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session.id)
        cid = uuid.uuid4()
        hold = await db.get(Participation, hold.id)
        waiter = await db.get(Participation, waiter.id)
        assert await expire_if_overdue(db, scope, hold, correlation_id=cid) == []
        assert await expire_if_overdue(db, scope, waiter, correlation_id=cid) == []
        assert await end_waiting_if_closed(db, scope, waiter, correlation_id=cid) == []
        assert await end_waiting_if_closed(db, scope, hold, correlation_id=cid) == []


async def test_late_attendee_action_meets_the_closed_entry(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1, window="closed")
        waiter = await make_waiting(db, session, await make_account(db), 1)

    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session.id)
        waiter = await db.get(Participation, waiter.id)
        changes = await end_waiting_if_closed(
            db, scope, waiter, correlation_id=uuid.uuid4()
        )
        assert [t.reason for t in changes] == ["cancelled"]
        assert waiter.end_reason == EndReason.REGISTRATION_CLOSED
