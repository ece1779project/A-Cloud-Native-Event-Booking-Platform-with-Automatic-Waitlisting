"""PRD Section 2 acceptance scenarios, run in-process against the real
services and worker scans: the capacity round, waitlist fairness, a replayed
old cancellation, two worker replicas and promotion latency.

``ACCEPTANCE_ROUNDS=10`` runs the ten capacity rounds the PRD asks for; the
normal suite runs one.
"""

import asyncio
import os
import random
import uuid
from collections import Counter

import pytest
from sqlalchemy import func, select

from app.cli.promotion_report import format_report, percentile, promotion_latencies
from app.core.errors import AppError, ErrorCode
from app.db.participation_store import capacity_violations, session_counts
from app.domain.enums import ParticipationState as S
from app.domain.transitions import ACTIVE_STATES
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange
from app.services import booking
from app.services import participations as psvc
from app.workers.__main__ import build_scans
from app.workers.expiry import ExpiryScan
from app.workers.promotion import PromotionScan
from app.workers.runner import run_scan_loop
from tests.factories import (
    make_account,
    make_held,
    make_offered,
    make_scope,
    make_waiting,
)
from tests.helpers import (
    OFFER_VALIDITY,
    expire,
    fifo_violations,
    promote,
    reload,
    session_version,
)

pytestmark = pytest.mark.usefixtures("capacity_invariant")

ROUNDS = int(os.environ.get("ACCEPTANCE_ROUNDS", "1"))
CAPACITY, CLIENTS, COMMANDS = 20, 100, 1000


async def _count_changes(session_factory, session_id, to_state) -> int:
    async with session_factory() as db:
        return await db.scalar(
            select(func.count())
            .select_from(ParticipationChange)
            .join(
                Participation, Participation.id == ParticipationChange.participation_id
            )
            .where(
                Participation.session_id == session_id,
                ParticipationChange.to_state == to_state,
            )
        )


async def _scan_until_idle(
    session_factory, scans, *, interval: float, timeout: float, settle: float = 0
):
    """Run scan loops for at least ``settle`` seconds, then until no session
    has overdue rows or free seats with waiters."""
    stop = asyncio.Event()
    loops = [
        asyncio.create_task(
            run_scan_loop(s, interval=interval, batch_size=100, stop=stop)
        )
        for s in scans
    ]
    expiry, promotion = (
        ExpiryScan(session_factory, 1),
        PromotionScan(session_factory, 1),
    )
    try:
        async with asyncio.timeout(timeout):
            await asyncio.sleep(settle)
            while await expiry.find_candidates(1) or await promotion.find_candidates(1):
                await asyncio.sleep(interval)
    finally:
        stop.set()
        await asyncio.gather(*loops)


# ---- Capacity and duplicate bookings (PRD Section 2, first row) ----


async def _client(session_factory, settings, session_id, account, rng, stats):
    """Send COMMANDS / CLIENTS commands, each chosen from the account's current
    state. About 5% resend the previous key, which must replay its outcome."""
    sent: dict[uuid.UUID, tuple] = {}
    last = None
    done = 0
    while done < COMMANDS // CLIENTS:
        if last and rng.random() < 0.05:
            command, key = last
        else:
            command = await _pick(session_factory, settings, session_id, account, rng)
            if command is None:  # let a hold or offer run down, or keep waiting
                await asyncio.sleep(rng.uniform(0.1, 0.5))
                continue
            key = uuid.uuid4()
        try:
            outcome = await command(key)
        except AppError as exc:
            assert exc.code == ErrorCode.LOCK_TIMEOUT, exc.code
            stats["LOCK_TIMEOUT"] += 1
        else:
            result = (outcome.http_status, outcome.result_code, outcome.payload)
            assert sent.setdefault(key, result) == result
            stats[outcome.result_code] += 1
        last = (command, key)
        done += 1


async def _pick(session_factory, settings, session_id, account, rng):
    try:
        view = await psvc.my_participation(session_factory, account, session_id)
    except AppError:
        view = None
    state = None if view is None or view.historical else view.state

    def on(service, **kw):
        return lambda key: service(
            session_factory, settings, account=account, request_id=key, **kw
        )

    book = on(booking.book, session_id=session_id)
    join = on(booking.join_waitlist, session_id=session_id)
    if state is None:
        return rng.choice([book, book, join])
    p = {"participation_id": view.id}
    pay = on(psvc.confirm, simulation_result="success", **p)
    fail = on(psvc.confirm, simulation_result="failure", **p)
    cancel, decline, leave = (
        on(c, **p) for c in (psvc.cancel, psvc.decline, psvc.leave)
    )
    options = {
        S.HELD: [pay, pay, fail, cancel, None],
        S.OFFERED: [pay, decline, None],
        S.WAITLISTED: [leave, join, None, None],  # join again is a duplicate
        S.CONFIRMED: [cancel, pay],  # pay again is a duplicate
    }[state]
    return rng.choice(options)


async def _watch(session_factory, stop, seen):
    while not stop.is_set():
        async with session_factory() as db:
            seen += await capacity_violations(db)
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("round_no", range(ROUNDS))
async def test_capacity_round(session_factory, settings, round_no):
    short = settings.model_copy(
        update={"hold_validity_seconds": 2, "offer_validity_seconds": 2}
    )
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=CAPACITY)
        accounts = [await make_account(db) for _ in range(CLIENTS)]
    seed = random.randrange(2**32)  # reproduces the command mix, not the interleaving
    stats, seen, stop = Counter(), [], asyncio.Event()
    background = [
        asyncio.create_task(
            run_scan_loop(scan, interval=0.2, batch_size=100, stop=stop)
        )
        for scan in (ExpiryScan(session_factory, 2), PromotionScan(session_factory, 2))
    ] + [asyncio.create_task(_watch(session_factory, stop, seen))]

    try:
        await asyncio.gather(
            *(
                _client(
                    session_factory,
                    short,
                    session.id,
                    a,
                    random.Random(seed + i),
                    stats,
                )
                for i, a in enumerate(accounts)
            )
        )
    finally:
        stop.set()
        await asyncio.gather(*background)
    await expire(session_factory, session.id)
    await promote(session_factory, session.id)
    print(f"round {round_no} seed={seed} {dict(stats)}")

    assert sum(stats.values()) == COMMANDS
    assert seen == []
    async with session_factory() as db:
        duplicates = await db.scalars(
            select(Participation.account_id)
            .where(
                Participation.session_id == session.id,
                Participation.state.in_(ACTIVE_STATES),
            )
            .group_by(Participation.account_id)
            .having(func.count() > 1)
        )
        assert list(duplicates) == []
    assert await fifo_violations(session_factory) == []
    assert session.id not in await PromotionScan(session_factory, 2).find_candidates(
        100
    )


# ---- Waitlist fairness, the cases other files do not cover ----


async def test_the_queue_head_withdraws_and_the_next_waiter_gets_the_seat(
    session_factory, settings
):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        h, a, b = [await make_account(db) for _ in range(3)]
        hold = await make_held(db, session, h)
        head = await make_waiting(db, session, a, 1)
        second = await make_waiting(db, session, b, 2)

    for account, p, command in ((a, head, psvc.leave), (h, hold, psvc.cancel)):
        await command(
            session_factory,
            settings,
            account=account,
            participation_id=p.id,
            request_id=uuid.uuid4(),
        )

    head, second = await reload(session_factory, [head, second])
    assert (head.state, second.state) == (S.CANCELLED, S.OFFERED)


async def test_an_expired_offer_passes_on_and_does_not_requeue(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a, b = await make_account(db), await make_account(db)
        offer = await make_offered(db, session, a, 1, overdue=True)
        second = await make_waiting(db, session, b, 2)

    await expire(session_factory, session.id)

    offer, second = await reload(session_factory, [offer, second])
    assert (offer.state, second.state) == (S.EXPIRED, S.OFFERED)
    async with session_factory() as db:
        rows = await db.scalars(
            select(Participation).where(Participation.account_id == a.id)
        )
        assert [p.id for p in rows] == [offer.id]  # FR-07.10: no new queue entry


# ---- An old cancellation replayed after a new booking ----


async def test_old_cancellation_replayed_after_a_new_booking(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)

    def book():
        return booking.book(
            session_factory,
            settings,
            account=a,
            session_id=session.id,
            request_id=uuid.uuid4(),
        )

    def cancel(p_id, key):
        return psvc.cancel(
            session_factory, settings, account=a, participation_id=p_id, request_id=key
        )

    old = await book()
    key = uuid.uuid4()
    first = await cancel(old.participation_id, key)
    new = await book()
    version = await session_version(session_factory, session.id)

    replayed = await cancel(old.participation_id, key)
    repeated = await cancel(old.participation_id, uuid.uuid4())

    assert replayed.replayed and replayed.payload == first.payload
    assert (repeated.result_code, repeated.changes) == ("CANCELLED", [])
    assert await session_version(session_factory, session.id) == version
    async with session_factory() as db:
        assert (await db.get(Participation, new.participation_id)).state == S.HELD
        assert (await session_counts(db, session.id)).occupied == 1


# ---- Two worker replicas ----


async def test_two_worker_replicas_share_a_backlog(session_factory, settings):
    sessions = []
    async with session_factory.begin() as db:
        for _ in range(50):
            _, _, session = await make_scope(db, capacity=1)
            await make_held(db, session, await make_account(db), overdue=True)
            await make_waiting(db, session, await make_account(db), 1)
            sessions.append(session)

    replicas = build_scans(session_factory, settings) + build_scans(
        session_factory, settings
    )
    await _scan_until_idle(session_factory, replicas, interval=0.05, timeout=15)

    for session in sessions:
        assert await _count_changes(session_factory, session.id, S.EXPIRED) == 1
        assert await _count_changes(session_factory, session.id, S.OFFERED) == 1
    assert await fifo_violations(session_factory) == []


# ---- Promotion latency (PRD Section 2, third row) ----


async def test_promotion_latency_meets_the_target(session_factory, settings):
    """120 holds expire over about 8 s, fewer than 20 per one-second scan, so
    each expiry pass offers every seat it frees. Then 120 offers are released
    by decline or by confirm and cancel."""
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=120)
        for i in range(120):  # deadlines 1 to 8 seconds from now
            await make_held(
                db, session, await make_account(db), minutes=(1 + 7 * i / 120) / 60
            )
        waiters = {}
        for seq in range(1, 241):
            account = await make_account(db)
            waiters[account.id] = account
            await make_waiting(db, session, account, seq)

    await _scan_until_idle(
        session_factory,
        [ExpiryScan(session_factory, OFFER_VALIDITY)],
        interval=1,
        timeout=30,
        settle=8.5,
    )

    async with session_factory() as db:
        offers = list(
            await db.scalars(
                select(Participation).where(
                    Participation.session_id == session.id,
                    Participation.state == S.OFFERED,
                )
            )
        )
    assert len(offers) == 120

    async def release(i, p):
        account = waiters[p.account_id]
        kw = {"account": account, "participation_id": p.id}
        if i % 2:
            await psvc.decline(session_factory, settings, request_id=uuid.uuid4(), **kw)
        else:
            await psvc.confirm(
                session_factory,
                settings,
                simulation_result="success",
                request_id=uuid.uuid4(),
                **kw,
            )
            await psvc.cancel(session_factory, settings, request_id=uuid.uuid4(), **kw)

    await asyncio.gather(*(release(i, p) for i, p in enumerate(offers)))

    async with session_factory() as db:
        report = await promotion_latencies(db, session_id=session.id)
    print(format_report(report))
    for category in ("expiration", "cancellation"):
        values = report.seconds[category]
        assert len(values) >= 100, category
        assert percentile(values, 0.95) <= 5, category
