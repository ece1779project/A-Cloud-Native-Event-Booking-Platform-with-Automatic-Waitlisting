"""Competing connections: lock waits, timeouts, the clock rule and no oversell."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.engine import is_retryable_timeout
from app.db.locks import lock_session_scope
from app.db.participation_store import bump_version, capacity_violations, session_counts
from app.domain.enums import ParticipationState
from app.models.participation import Participation
from tests.factories import make_account, make_held, make_scope


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
    """The scope's clock is read after the lock wait, not at transaction start."""
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
            scope = await lock_session_scope(db, session.id)
            return scope.now - started

    _, waited = await asyncio.gather(hold(), wait())
    assert waited >= timedelta(seconds=0.3)


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
            scope = await lock_session_scope(
                db, session.id, share_account_ids=[account_id]
            )
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
