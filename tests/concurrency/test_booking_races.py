"""Races over independent connections against the real booking commands."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.db.participation_store import capacity_violations, session_counts
from app.models.participation import Participation
try:
    from app.services import booking, participations as psvc, sessions as ssvc
    from app.schemas.sessions import SessionPatch
    from app.db.locks import lock_session_scope
    from app.db.participation_store import bump_version
    from app.domain.timing import is_overdue
    from app.services.expiration import expire_attempt
except ImportError as exc:
    pytest.skip(f"第 08/09 章未完成: {exc}", allow_module_level=True)
from tests.factories import make_account, make_scope


async def _world(session_factory, *, capacity: int, attendees: int):
    async with session_factory.begin() as db:
        organizer, event, session = await make_scope(db, capacity=capacity)
        people = [await make_account(db) for _ in range(attendees)]
    return organizer, event, session, people


async def _assert_invariant(session_factory):
    async with session_factory() as db:
        assert await capacity_violations(db) == []


async def test_last_seat_has_one_winner(session_factory, settings):
    _, _, session, people = await _world(session_factory, capacity=1, attendees=30)

    results = await asyncio.gather(*[
        booking.book(session_factory, settings, account=a, session_id=session.id, request_id=uuid.uuid4())
        for a in people])

    codes = [r.result_code for r in results]
    assert codes.count("HELD") == 1
    assert codes.count("WAITLIST_REQUIRED") == 29
    await _assert_invariant(session_factory)


async def test_same_user_many_tabs_one_attempt(session_factory, settings):
    _, _, session, (a,) = await _world(session_factory, capacity=5, attendees=1)

    results = await asyncio.gather(*[
        booking.book(session_factory, settings, account=a, session_id=session.id, request_id=uuid.uuid4())
        for _ in range(10)])

    assert sorted(r.http_status for r in results) == [200] * 9 + [201]
    assert len({r.participation_id for r in results}) == 1
    async with session_factory() as db:
        n = await db.scalar(select(func.count()).where(Participation.account_id == a.id))
    assert n == 1


async def test_same_key_concurrent_retries(session_factory, settings):
    _, _, session, (a,) = await _world(session_factory, capacity=5, attendees=1)
    key = uuid.uuid4()
    results = await asyncio.gather(*[
        booking.book(session_factory, settings, account=a, session_id=session.id, request_id=key)
        for _ in range(5)])
    assert {r.http_status for r in results} == {201}
    assert sum(not r.replayed for r in results) == 1


async def _worker_expire(session_factory, participation_id, session_id):
    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session_id)
        p = await db.get(Participation, participation_id, populate_existing=True)
        if p.state in ("HELD", "OFFERED") and is_overdue(p.deadline_at, scope.now):
            await expire_attempt(db, p, now=scope.now, correlation_id=uuid.uuid4())
            await bump_version(db, scope.session)
            return "EXPIRED"
        return "NOOP"   # stale candidate


async def test_confirm_vs_expire_single_outcome(session_factory, settings):
    short = settings.model_copy(update={"hold_validity_seconds": 1})
    for _ in range(10):
        _, _, session, (a,) = await _world(session_factory, capacity=1, attendees=1)
        held = await booking.book(session_factory, short, account=a, session_id=session.id,
                                  request_id=uuid.uuid4())
        await asyncio.sleep(0.98)

        confirm, worker = await asyncio.gather(
            psvc.confirm(session_factory, short, account=a, participation_id=held.participation_id,
                         simulation_result="success", request_id=uuid.uuid4()),
            _worker_expire(session_factory, held.participation_id, session.id),
        )
        async with session_factory() as db:
            p = await db.get(Participation, held.participation_id)
        if confirm.result_code == "CONFIRMED":
            assert p.state == "CONFIRMED" and worker == "NOOP"
            assert p.confirmed_at < p.deadline_at
        else:
            assert confirm.result_code == "PARTICIPATION_EXPIRED"
            assert p.state == "EXPIRED"
        await _assert_invariant(session_factory)


async def test_capacity_reduction_vs_booking(session_factory, settings):
    organizer, _, session, people = await _world(session_factory, capacity=3, attendees=3)
    for a in people[:2]:
        await booking.book(session_factory, settings, account=a, session_id=session.id,
                           request_id=uuid.uuid4())

    async def shrink():
        try:
            await ssvc.patch_session(session_factory, settings, organizer, session.id,
                                     SessionPatch(capacity=2))
            return "SHRUNK"
        except Exception as exc:   # AppError(CAPACITY_BELOW_OCCUPIED)
            return getattr(exc, "code", exc)

    book, shrink_result = await asyncio.gather(
        booking.book(session_factory, settings, account=people[2], session_id=session.id,
                     request_id=uuid.uuid4()),
        shrink())

    assert (book.result_code, shrink_result) in {
        ("HELD", "CAPACITY_BELOW_OCCUPIED"),
        ("WAITLIST_REQUIRED", "SHRUNK"),
    }
    await _assert_invariant(session_factory)


async def test_concurrent_joins_get_unique_ordered_seqs(session_factory, settings):
    _, _, session, people = await _world(session_factory, capacity=1, attendees=21)
    await booking.book(session_factory, settings, account=people[0], session_id=session.id,
                       request_id=uuid.uuid4())

    results = await asyncio.gather(*[
        booking.join_waitlist(session_factory, settings, account=a, session_id=session.id,
                              request_id=uuid.uuid4()) for a in people[1:]])

    assert all(r.result_code == "WAITLISTED" for r in results)
    async with session_factory() as db:
        seqs = sorted(await db.scalars(select(Participation.queue_seq)
                                       .where(Participation.session_id == session.id,
                                              Participation.state == "WAITLISTED")))
    assert seqs == list(range(1, 21))
    positions = sorted(r.payload["queue_position"] for r in results)
    assert positions == list(range(1, 21))


async def test_cancel_promotes_queue_head_in_fifo(session_factory, settings):
    _, _, session, (a, b, c) = await _world(session_factory, capacity=1, attendees=3)
    held = await booking.book(session_factory, settings, account=a, session_id=session.id,
                              request_id=uuid.uuid4())
    for x in (b, c):
        await booking.join_waitlist(session_factory, settings, account=x, session_id=session.id,
                                    request_id=uuid.uuid4())

    await psvc.cancel(session_factory, settings, account=a, participation_id=held.participation_id,
                      request_id=uuid.uuid4())

    async with session_factory() as db:
        states = dict((await db.execute(
            select(Participation.account_id, Participation.state)
            .where(Participation.session_id == session.id,
                   Participation.state.in_(("OFFERED", "WAITLISTED"))))).all())
        counts = await session_counts(db, session.id)
    assert states == {b.id: "OFFERED", c.id: "WAITLISTED"}
    assert counts.occupied == 1
    await _assert_invariant(session_factory)
