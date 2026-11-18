"""Lock entry point and participation store helpers, single connection at a time."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db.engine import db_now
from app.db.locks import (
    RowMissing,
    lock_account_scope,
    lock_event_scope,
    lock_queue_head_account,
    lock_session_scope,
)
from app.db.participation_store import (
    active_attempt,
    apply_transition,
    attempts_in_states,
    bump_version,
    due_attempts,
    next_queue_seq,
    queue_head,
    record_change,
    session_counts,
    waitlist_position,
)
from app.domain.enums import EndReason, ParticipationState, TransitionReason
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange
from tests.factories import (
    make_account,
    make_confirmed,
    make_held,
    make_offered,
    make_scope,
    make_waiting,
)

S = ParticipationState


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
        assert scope.now is not None


async def test_account_and_queue_head_locks(session_factory):
    async with session_factory.begin() as db:
        a, b = await make_account(db), await make_account(db)

    async with session_factory.begin() as db:
        locked = await lock_account_scope(db, share_ids=[b.id], exclusive_ids=[a.id, b.id])
        assert list(locked.accounts) == sorted([a.id, b.id])
        head = await lock_queue_head_account(db, a.id)
        assert head.account.id == a.id
        assert head.now >= locked.now
        with pytest.raises(RowMissing):
            await lock_queue_head_account(db, uuid.uuid4())


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
        # The factory wrote change 1 (BOOKED); the next one is numbered 2.
        change = await record_change(
            db, participation_id=held.id, from_state=ParticipationState.HELD,
            to_state=ParticipationState.CONFIRMED, reason_code="PAYMENT_CONFIRMED",
            occurred_at=now, correlation_id=cid, actor_id=a.id,
        )
        assert change.change_no == 2

    async with session_factory.begin() as db:
        counts = await session_counts(db, session.id)
        assert (counts.held, counts.waiting, counts.occupied) == (1, 2, 1)
        assert (await queue_head(db, session.id)).id == wb.id
        assert await waitlist_position(db, wc) == 2
        assert await waitlist_position(db, held) is None
        assert (await active_attempt(db, session.id, c.id)).id == wc.id


async def _history(db, participation_id) -> list[ParticipationChange]:
    return list(
        await db.scalars(
            select(ParticipationChange)
            .where(ParticipationChange.participation_id == participation_id)
            .order_by(ParticipationChange.change_no)
        )
    )


async def test_apply_transition_sets_target_fields_and_appends_history(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=5)
        a, b, c, d = [await make_account(db) for _ in range(4)]
        waiting = await make_waiting(db, session, a, queue_seq=1)
        to_confirm = await make_held(db, session, b)
        to_expire = await make_held(db, session, c, overdue=True)
        to_decline = await make_offered(db, session, d, queue_seq=2)

    cid = uuid.uuid4()
    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session.id)
        now = scope.now
        deadline = now + timedelta(minutes=10)
        rows = {
            p.id: await db.get(Participation, p.id)
            for p in (waiting, to_confirm, to_expire, to_decline)
        }
        common = {"now": now, "correlation_id": cid}
        await apply_transition(
            db,
            rows[waiting.id],
            S.OFFERED,
            reason_code=TransitionReason.PROMOTED,
            actor_id=None,
            deadline_at=deadline,
            **common,
        )
        await apply_transition(
            db,
            rows[to_confirm.id],
            S.CONFIRMED,
            reason_code=TransitionReason.PAYMENT_CONFIRMED,
            actor_id=b.id,
            **common,
        )
        await apply_transition(
            db,
            rows[to_expire.id],
            S.EXPIRED,
            reason_code=EndReason.HOLD_EXPIRED,
            actor_id=None,
            **common,
        )
        await apply_transition(
            db,
            rows[to_decline.id],
            S.CANCELLED,
            reason_code=EndReason.OFFER_DECLINED,
            actor_id=d.id,
            **common,
        )

    # Committed, so the database trigger accepted every change.
    async with session_factory.begin() as db:
        offered = await db.get(Participation, waiting.id)
        assert (offered.state, offered.allocated_at, offered.deadline_at) == (
            S.OFFERED,
            now,
            deadline,
        )
        confirmed = await db.get(Participation, to_confirm.id)
        assert (confirmed.state, confirmed.confirmed_at) == (S.CONFIRMED, now)
        for row_id, state, reason in (
            (to_expire.id, S.EXPIRED, EndReason.HOLD_EXPIRED),
            (to_decline.id, S.CANCELLED, EndReason.OFFER_DECLINED),
        ):
            ended = await db.get(Participation, row_id)
            assert (ended.state, ended.ended_at, ended.end_reason) == (
                state,
                now,
                reason,
            )

        promoted = (await _history(db, waiting.id))[-1]
        assert (promoted.change_no, promoted.from_state, promoted.to_state) == (
            2,
            S.WAITLISTED,
            S.OFFERED,
        )
        assert (promoted.reason_code, promoted.actor_id, promoted.correlation_id) == (
            TransitionReason.PROMOTED,
            None,
            cid,
        )
        declined = (await _history(db, to_decline.id))[-1]
        assert (declined.change_no, declined.actor_id) == (3, d.id)


async def test_apply_transition_refuses_bad_requests_without_writing(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)
        a, b = await make_account(db), await make_account(db)
        held = await make_held(db, session, a)
        waiting = await make_waiting(db, session, b, queue_seq=1)

    async with session_factory.begin() as db:
        scope = await lock_session_scope(db, session.id)
        common = {"now": scope.now, "correlation_id": uuid.uuid4(), "actor_id": None}
        later = scope.now + timedelta(minutes=10)
        held = await db.get(Participation, held.id)
        waiting = await db.get(Participation, waiting.id)

        with pytest.raises(ValueError, match="illegal transition"):
            await apply_transition(
                db,
                held,
                S.OFFERED,
                reason_code=TransitionReason.PROMOTED,
                deadline_at=later,
                **common,
            )
        with pytest.raises(ValueError, match="deadline_at"):
            await apply_transition(
                db, waiting, S.OFFERED, reason_code=TransitionReason.PROMOTED, **common
            )
        with pytest.raises(ValueError, match="deadline_at"):
            await apply_transition(
                db,
                held,
                S.EXPIRED,
                reason_code=EndReason.HOLD_EXPIRED,
                deadline_at=later,
                **common,
            )

        assert (held.state, waiting.state) == (S.HELD, S.WAITLISTED)
        assert (
            await db.scalar(select(func.count()).select_from(ParticipationChange)) == 2
        )


async def test_due_attempts_includes_the_deadline_instant(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=5)
        a, b, c, d, e = [await make_account(db) for _ in range(5)]
        due_hold = await make_held(db, session, a, overdue=True)
        due_offer = await make_offered(db, session, b, queue_seq=1, overdue=True)
        await make_held(db, session, c)  # not due yet
        await make_confirmed(db, session, d)
        await make_waiting(db, session, e, queue_seq=2)

    async with session_factory.begin() as db:
        now = await db_now(db)
        due = await due_attempts(db, session.id, now)
        assert {p.id for p in due} == {due_hold.id, due_offer.id}
        assert [p.deadline_at for p in due] == sorted(p.deadline_at for p in due)

        exactly = await due_attempts(db, session.id, due_hold.deadline_at)
        assert due_hold.id in {p.id for p in exactly}
        before = await due_attempts(
            db, session.id, due_hold.deadline_at - timedelta(seconds=1)
        )
        assert due_hold.id not in {p.id for p in before}


async def test_attempts_in_states_orders_unqueued_rows_first(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=5)
        a, b, c, d = [await make_account(db) for _ in range(4)]
        second = await make_waiting(db, session, a, queue_seq=2)
        first = await make_waiting(db, session, b, queue_seq=1)
        held = await make_held(db, session, c)
        await make_confirmed(db, session, d)

    async with session_factory.begin() as db:
        rows = await attempts_in_states(db, session.id, (S.HELD, S.WAITLISTED))
        assert [p.id for p in rows] == [held.id, first.id, second.id]
