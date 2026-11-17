"""Workers racing each other and attendee-held locks, over independent
connections (DATABASE_DESIGN Section 7). Every test ends with the
capacity-invariant query."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.locks import lock_session_scope
from app.domain.enums import EndReason, ParticipationState
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange
from app.services.expiration import close_session, expire_session, promote_session
from app.workers.expiry import ExpiryScan
from app.workers.runner import run_scan_loop
from tests.factories import (
    make_account,
    make_event,
    make_held,
    make_offered,
    make_scope,
    make_session,
    make_waiting,
)

pytestmark = pytest.mark.usefixtures("capacity_invariant")

S = ParticipationState
VALIDITY = 600


def _expire(session_factory, session_id):
    return expire_session(session_factory, session_id, offer_validity_seconds=VALIDITY)


async def _rows(session_factory, session_id):
    async with session_factory.begin() as db:
        return list(
            await db.scalars(
                select(Participation)
                .where(Participation.session_id == session_id)
                .order_by(Participation.queue_seq.nulls_first())
            )
        )


async def _count_changes(session_factory, to_state):
    async with session_factory.begin() as db:
        return await db.scalar(
            select(func.count())
            .select_from(ParticipationChange)
            .where(ParticipationChange.to_state == to_state)
        )


async def test_two_expiry_workers_on_one_session(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_held(db, session, await make_account(db), overdue=True)
        await make_waiting(db, session, await make_account(db), 1)

    results = await asyncio.gather(
        _expire(session_factory, session.id), _expire(session_factory, session.id)
    )

    assert sorted(r is None for r in results) == [False, True]
    assert [p.state for p in await _rows(session_factory, session.id)] == [
        S.EXPIRED,
        S.OFFERED,
    ]
    assert await _count_changes(session_factory, S.EXPIRED) == 1


async def test_ten_promoters_race_for_three_seats(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=3)
        for seq in range(1, 9):
            await make_waiting(db, session, await make_account(db), seq)

    results = await asyncio.gather(
        *(
            promote_session(
                session_factory, session.id, offer_validity_seconds=VALIDITY
            )
            for _ in range(10)
        )
    )

    assert sum(r is not None for r in results) == 1
    offered = [
        p.queue_seq
        for p in await _rows(session_factory, session.id)
        if p.state == S.OFFERED
    ]
    assert offered == [1, 2, 3]
    assert await _count_changes(session_factory, S.OFFERED) == 3


async def test_expiry_waits_for_a_lock_held_across_the_deadline(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        hold = await make_held(db, session, await make_account(db), minutes=1 / 60)

    locked = asyncio.Event()

    async def holder():
        async with session_factory.begin() as db:
            await lock_session_scope(db, session.id)
            locked.set()
            await asyncio.sleep(1.5)  # the deadline passes while we hold the lock
            row = await db.get(Participation, hold.id)
            assert row.state == S.HELD

    async def worker():
        await locked.wait()
        return await _expire(session_factory, session.id)

    _, first, second = await asyncio.gather(holder(), worker(), worker())

    assert sorted(r is None for r in (first, second)) == [False, True]
    (expired,) = await _rows(session_factory, session.id)
    assert (expired.state, expired.end_reason) == (S.EXPIRED, EndReason.HOLD_EXPIRED)
    assert await _count_changes(session_factory, S.EXPIRED) == 1


async def test_expiry_racing_closure_at_the_close_instant(session_factory):
    closes_at = datetime.now(UTC) + timedelta(seconds=1)
    async with session_factory.begin() as db:
        event = await make_event(db, await make_account(db))
        # make_session puts the close one hour after ``now``.
        session = await make_session(
            db, event, capacity=1, now=closes_at - timedelta(hours=1)
        )
        await make_held(db, session, await make_account(db), minutes=0.5 / 60)
        await make_waiting(db, session, await make_account(db), 1)

    await asyncio.sleep((closes_at - datetime.now(UTC)).total_seconds() + 0.1)
    await asyncio.gather(
        _expire(session_factory, session.id), close_session(session_factory, session.id)
    )

    held, waiter = await _rows(session_factory, session.id)
    assert (held.state, held.end_reason) == (S.EXPIRED, EndReason.HOLD_EXPIRED)
    assert (waiter.state, waiter.end_reason) == (
        S.CANCELLED,
        EndReason.REGISTRATION_CLOSED,
    )
    assert await _count_changes(session_factory, S.OFFERED) == 0
    assert await _count_changes(session_factory, S.EXPIRED) == 1
    assert await _count_changes(session_factory, S.CANCELLED) == 1


async def test_a_fresh_runner_clears_an_overdue_backlog(session_factory):
    """FR-07.11: work left by a stopped worker is rediscovered from PostgreSQL."""
    sessions = []
    async with session_factory.begin() as db:
        for _ in range(3):
            _, _, session = await make_scope(db, capacity=2)
            await make_held(db, session, await make_account(db), overdue=True)
            await make_offered(db, session, await make_account(db), 1, overdue=True)
            await make_waiting(db, session, await make_account(db), 2)
            sessions.append(session)
    deadlines = {
        p.id: p.deadline_at
        for s in sessions
        for p in await _rows(session_factory, s.id)
        if p.deadline_at is not None
    }

    scan = ExpiryScan(session_factory, VALIDITY)
    stop = asyncio.Event()
    loop = asyncio.create_task(
        run_scan_loop(scan, interval=0.05, batch_size=2, stop=stop)
    )
    try:
        async with asyncio.timeout(5):
            while await scan.find_candidates(10):
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await loop

    for session in sessions:
        states = [p.state for p in await _rows(session_factory, session.id)]
        assert sorted(states) == [S.EXPIRED, S.EXPIRED, S.OFFERED]
        for p in await _rows(session_factory, session.id):
            if p.id in deadlines:
                assert p.deadline_at == deadlines[p.id]  # never rewritten
