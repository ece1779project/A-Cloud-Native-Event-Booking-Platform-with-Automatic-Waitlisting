"""Decline and leave racing the expiry worker, confirmation, promotion and
each other, over independent connections. Every test ends with the
capacity-invariant query and the FIFO audit."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.db.participation_store import session_counts
from app.domain.enums import ParticipationState as S
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange
from app.services import booking
from app.services import participations as psvc
from tests.factories import (
    make_account,
    make_held,
    make_offered,
    make_scope,
    make_waiting,
)
from tests.helpers import expire, fifo_violations, reload

pytestmark = pytest.mark.usefixtures("capacity_invariant")

ROUNDS = 10  # timing-sensitive races run several times


@pytest.fixture(autouse=True)
async def _fifo_audit(session_factory):
    yield
    assert await fifo_violations(session_factory) == []


def _run(command, session_factory, settings, account, p, key=None, **kw):
    return command(
        session_factory, settings, account=account, participation_id=p.id,
        request_id=key or uuid.uuid4(), **kw,
    )


async def _offers_made(session_factory, p) -> int:
    async with session_factory() as db:
        return await db.scalar(
            select(func.count()).where(
                ParticipationChange.participation_id == p.id,
                ParticipationChange.to_state == S.OFFERED,
            )
        )


async def test_decline_vs_expiry_at_the_deadline(session_factory, settings):
    for _ in range(ROUNDS):
        async with session_factory.begin() as db:
            _, _, session = await make_scope(db, capacity=1)
            a, b = await make_account(db), await make_account(db)
            offer = await make_offered(db, session, a, 1, minutes=1 / 60)
            waiter = await make_waiting(db, session, b, 2)
        await asyncio.sleep(0.98)

        declined, worker = await asyncio.gather(
            _run(psvc.decline, session_factory, settings, a, offer),
            expire(session_factory, session.id),
        )

        if declined.result_code == "CANCELLED":
            assert worker is None
        else:
            assert declined.result_code == "PARTICIPATION_EXPIRED"
        assert await _offers_made(session_factory, waiter) == 1


async def test_decline_vs_confirm_from_two_tabs(session_factory, settings):
    for _ in range(ROUNDS):
        async with session_factory.begin() as db:
            _, _, session = await make_scope(db, capacity=1)
            a, b = await make_account(db), await make_account(db)
            offer = await make_offered(db, session, a, 1)
            await make_waiting(db, session, b, 2)

        confirmed, declined = await asyncio.gather(
            _run(psvc.confirm, session_factory, settings, a, offer, simulation_result="success"),
            _run(psvc.decline, session_factory, settings, a, offer),
        )

        assert {confirmed.result_code, declined.result_code} in (
            {"CONFIRMED", "INVALID_STATE_TRANSITION"},
            {"INVALID_STATE_TRANSITION", "CANCELLED"},
        )
        async with session_factory() as db:
            assert (await session_counts(db, session.id)).occupied == 1


async def test_queue_head_leaves_while_the_holder_cancels(session_factory, settings):
    for _ in range(ROUNDS):
        async with session_factory.begin() as db:
            _, _, session = await make_scope(db, capacity=1)
            h, a, b = [await make_account(db) for _ in range(3)]
            hold = await make_held(db, session, h)
            head = await make_waiting(db, session, a, 1)
            second = await make_waiting(db, session, b, 2)

        _, left = await asyncio.gather(
            _run(psvc.cancel, session_factory, settings, h, hold),
            _run(psvc.leave, session_factory, settings, a, head),
        )

        head, second = await reload(session_factory, [head, second])
        if left.result_code == "CANCELLED":
            assert (head.state, second.state) == (S.CANCELLED, S.OFFERED)
        else:
            assert left.result_code == "INVALID_STATE_TRANSITION"
            assert (head.state, second.state) == (S.OFFERED, S.WAITLISTED)


async def test_concurrent_declines_offer_seats_in_queue_order(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=5)
        offers = []
        for seq in range(1, 6):
            account = await make_account(db)
            offers.append((account, await make_offered(db, session, account, seq)))
        for seq in range(6, 16):
            await make_waiting(db, session, await make_account(db), seq)

    await asyncio.gather(
        *(_run(psvc.decline, session_factory, settings, a, p) for a, p in offers)
    )

    async with session_factory() as db:
        offered = await db.scalars(
            select(Participation.queue_seq)
            .where(Participation.session_id == session.id, Participation.state == S.OFFERED)
            .order_by(Participation.queue_seq)
        )
        assert list(offered) == [6, 7, 8, 9, 10]


async def test_leaves_and_joins_keep_the_queue_consistent(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_held(db, session, await make_account(db))
        originals = []
        for seq in range(1, 31):
            account = await make_account(db)
            originals.append((account, await make_waiting(db, session, account, seq)))
        newcomers = [await make_account(db) for _ in range(20)]
        session.last_queue_seq = 30

    leaving, staying = originals[::3] + originals[1::3], originals[2::3]  # 20 leave
    results = await asyncio.gather(
        *(_run(psvc.leave, session_factory, settings, a, p) for a, p in leaving),
        *(
            booking.join_waitlist(
                session_factory, settings, account=a, session_id=session.id,
                request_id=uuid.uuid4(),
            )
            for a in newcomers
        ),
    )

    assert {r.result_code for r in results} == {"CANCELLED", "WAITLISTED"}
    queue = [(a, p.id) for a, p in staying] + [(a, r.participation_id)
                                               for a, r in zip(newcomers, results[20:])]
    views = [await psvc.get_participation(session_factory, a, pid) for a, pid in queue]
    positions = sorted(v.queue_position for v in views)
    assert positions == list(range(1, 31))
    # Every original still waiting is ahead of every newcomer.
    assert max(v.queue_position for v in views[:10]) == 10


async def test_concurrent_decline_retries_run_once(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        offer = await make_offered(db, session, a, 1)
    key = uuid.uuid4()

    results = await asyncio.gather(
        *(_run(psvc.decline, session_factory, settings, a, offer, key) for _ in range(5))
    )

    assert sum(not r.replayed for r in results) == 1
    first = results[0]
    assert all(
        (r.http_status, r.result_code, r.payload)
        == (first.http_status, first.result_code, first.payload)
        for r in results
    )
