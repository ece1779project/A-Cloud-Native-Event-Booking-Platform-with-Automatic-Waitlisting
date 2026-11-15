"""Lock entry point, clock rule, store helpers and the no-oversell invariant."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.infra.db import db_now, is_retryable_timeout
from app.modules.booking.locking import (
    RowMissing,
    lock_event_scope,
    lock_session_scope,
)
from app.modules.booking.models import Participation, ParticipationState
from app.modules.booking.store import (
    active_attempt,
    bump_version,
    capacity_violations,
    next_queue_seq,
    queue_head,
    record_change,
    session_counts,
    waitlist_position,
)
from tests.db.factories import make_account, make_held, make_scope, make_waiting


async def test_lock_session_scope_returns_locked_rows(session_factory):
    async with session_factory.begin() as db:
        organizer, event, session = await make_scope(db)
        attendee = await make_account(db)

    async with session_factory.begin() as db:
        scope = await lock_session_scope(
            db, session.id, share_account_ids=[attendee.id, organizer.id]
        )
        assert scope.event.id == event.id
        assert scope.session.id == session.id
        assert list(scope.accounts) == sorted([attendee.id, organizer.id])


async def test_missing_rows_raise(session_factory):
    async with session_factory.begin() as db:
        with pytest.raises(RowMissing):
            await lock_session_scope(db, uuid.uuid4())
    async with session_factory.begin() as db:
        with pytest.raises(ValueError):
            await lock_event_scope(db, uuid.uuid4(), all_sessions=True)


async def test_lock_timeout_is_retryable(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)

    async with session_factory.begin() as holder:
        await lock_session_scope(holder, session.id)
        with pytest.raises(DBAPIError) as info:
            async with session_factory.begin() as waiter:
                await waiter.execute(text("SET LOCAL lock_timeout = '200ms'"))
                await lock_session_scope(waiter, session.id)
    assert is_retryable_timeout(info.value)


async def test_db_now_reflects_lock_wait(session_factory):
    """clock_timestamp() after the lock is later than the transaction start."""
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)

    locked = asyncio.Event()

    async def hold():
        async with session_factory.begin() as db:
            await lock_session_scope(db, session.id)
            locked.set()
            await asyncio.sleep(0.4)

    async def wait():
        await locked.wait()
        async with session_factory.begin() as db:
            started = await db.scalar(select(func.now()))
            await lock_session_scope(db, session.id)
            return await db_now(db) - started

    _, waited = await asyncio.gather(hold(), wait())
    assert waited >= timedelta(seconds=0.3)


async def test_store_helpers(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a, b, c = [await make_account(db) for _ in range(3)]

    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session.id)
        held = await make_held(db, scope.session, a)
        wb = await make_waiting(db, scope.session, b, await next_queue_seq(db, scope.session))
        wc = await make_waiting(db, scope.session, c, await next_queue_seq(db, scope.session))
        assert (wb.queue_seq, wc.queue_seq) == (1, 2)
        assert await bump_version(db, scope.session) == 1

        now = await db_now(db)
        cid = uuid.uuid4()
        first = await record_change(
            db, participation_id=held.id, from_state=None,
            to_state=ParticipationState.HELD, reason_code="BOOKED",
            occurred_at=now, correlation_id=cid, actor_id=a.id,
        )
        second = await record_change(
            db, participation_id=held.id, from_state=ParticipationState.HELD,
            to_state=ParticipationState.CONFIRMED, reason_code="PAYMENT_CONFIRMED",
            occurred_at=now, correlation_id=cid, actor_id=a.id,
        )
        assert (first.change_no, second.change_no) == (1, 2)

    async with session_factory.begin() as db:
        counts = await session_counts(db, session.id)
        assert (counts.held, counts.waiting, counts.occupied) == (1, 2, 1)
        assert (await queue_head(db, session.id)).id == wb.id
        assert await waitlist_position(db, wc) == 2
        assert await waitlist_position(db, held) is None
        assert (await active_attempt(db, session.id, c.id)).id == wc.id


async def test_concurrent_holds_never_oversell(session_factory):
    """Many writers racing for few seats through the lock entry point.

    This is a schema-level smoke test of the locking discipline; the full
    FR-04/05 load test runs against the booking command once it exists.
    """
    capacity, clients = 5, 40
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=capacity)
        attendees = [await make_account(db) for _ in range(clients)]

    async def try_hold(account_id):
        async with session_factory.begin() as db:
            scope = await lock_session_scope(db, session.id, share_account_ids=[account_id])
            counts = await session_counts(db, session.id)
            if counts.occupied >= scope.session.capacity:
                return False
            await make_held(db, scope.session, scope.accounts[account_id])
            await bump_version(db, scope.session)
            return True

    results = await asyncio.gather(*(try_hold(a.id) for a in attendees))

    assert sum(results) == capacity
    async with session_factory.begin() as db:
        assert await capacity_violations(db) == []
        held = await db.scalar(
            select(func.count()).where(
                Participation.session_id == session.id,
                Participation.state == ParticipationState.HELD,
            )
        )
        assert held == capacity
