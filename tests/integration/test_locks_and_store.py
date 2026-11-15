"""Lock entry point and participation store helpers, single connection at a time."""

import uuid

import pytest

from app.db.engine import db_now
from app.db.locks import RowMissing, lock_event_scope, lock_session_scope
from app.db.participation_store import (
    active_attempt,
    bump_version,
    next_queue_seq,
    queue_head,
    record_change,
    session_counts,
    waitlist_position,
)
from app.models.participation import ParticipationState
from tests.factories import make_account, make_held, make_scope, make_waiting


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
